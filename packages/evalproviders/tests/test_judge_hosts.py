# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P4-J14: pinned hosts and key binding (ADR 0030 rules 14 and 15, ADR 0024 amendment A12,
BL-142; contract docs/phase4/api-surface-batch-j1.md section 11).

The owner writes the judge allowlist; the judge configuration may name only an entry. Before any
key is read the entry must match exactly (URL and profile), the profile must be allowed for a real
run, and every DNS answer of a public entry must be public; the key is read by its variable name
and is bound to its host. https only, except a literal loopback address; no redirect, no proxy
environment, TLS verified. Nothing here resolves a name or opens a socket: the resolver is a fake.
"""

from __future__ import annotations

import ast
import inspect
import json
import os
import pickle
import ssl
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import httpx
import pytest

from evalproviders_p4j_support import (  # type: ignore[import-not-found,unused-ignore]
    CANARY_KEY,
    JudgeRig,
    context,
    core,
    jmod,
    openai_reply,
    request,
)
from evalproviders_support import ScriptedTransport, Sleeps, Ticker, WallClock  # type: ignore[import-not-found,unused-ignore]

KEY_A = "-".join(["CANARYKEY", "".join(["aa", "11", "zq"]), "".join(["7f", "c3", "d1"])])
KEY_B = "-".join(["CANARYKEY", "".join(["bb", "22", "yp"]), "".join(["9a", "e2", "b8"])])
URL_A = "https://api.openai.com/v1/chat/completions"
URL_B = "https://openrouter.ai/api/v1/chat/completions"
URL_LOOP = "http://127.0.0.1:8123/v1/chat/completions"
ALLOWLIST = f"""\
# the owner's judge allowlist
{URL_A} profile=openai/1 key_env=JUDGE_KEY_A
{URL_B} profile=openrouter/1 key_env=JUDGE_KEY_B  # a trailing comment
{URL_LOOP} profile=local_loopback/1
"""
PUBLIC = ["8.8.8.8", "2606:4700:4700::1111"]


class SpyEnviron(Mapping[str, str]):
    """An environment that records every read; a refusal before the key read leaves it empty."""

    def __init__(self, values: Mapping[str, str]) -> None:
        self._values = dict(values)
        self.reads: list[str] = []

    def __getitem__(self, key: str) -> str:
        self.reads.append(key)
        return self._values[key]

    def get(self, key: str, default: Any = None) -> Any:
        self.reads.append(key)
        return self._values.get(key, default)

    def __contains__(self, key: object) -> bool:
        self.reads.append(str(key))
        return key in self._values

    def __iter__(self) -> Iterator[str]:
        self.reads.append("<iter>")
        return iter(self._values)

    def __len__(self) -> int:
        self.reads.append("<len>")
        return len(self._values)


class SpyResolver:
    def __init__(self, answers: Mapping[str, list[str]] | None = None) -> None:
        self.answers = dict(answers or {})
        self.calls: list[tuple[str, int]] = []

    def __call__(self, host: str, port: int) -> list[str]:
        self.calls.append((host, port))
        if host not in self.answers:
            return list(PUBLIC)
        return list(self.answers[host])


def entries(text: str = ALLOWLIST) -> Any:
    return jmod("judgehosts").parse_judge_allowlist(text)


def refusal(call: Any, error: str = "JudgeAllowlistError") -> str:
    owner = jmod("judgeprofiles") if error == "JudgeProfileError" else jmod("judgehosts")
    with pytest.raises(getattr(owner, error)) as caught:
        call()
    assert str(caught.value) == caught.value.code, "the message is the stable code"
    assert caught.value.__cause__ is None
    for canary in (KEY_A, KEY_B, CANARY_KEY):
        assert canary not in str(caught.value) + repr(caught.value)
    return str(caught.value.code)


def bind(
    url: str = URL_A,
    profile: str = "openai/1",
    *,
    text: str = ALLOWLIST,
    environ: Any = None,
    resolver: Any = None,
    gate: bool = True,
    **more: Any,
) -> Any:
    return jmod("judgehosts").bind_endpoint(
        entries(text),
        url=url,
        profile=profile,
        environ={"JUDGE_KEY_A": KEY_A, "JUDGE_KEY_B": KEY_B} if environ is None else environ,
        resolver=SpyResolver() if resolver is None else resolver,
        first_request_gate=gate,
        **more,
    )


def test_judge_host_not_on_allowlist_refused_before_any_key_read() -> None:
    """Invariant I5: a URL or profile outside the owner's entries is refused with a stable code
    before the environment, the env file or the resolver is touched; so is an unverified
    profile without the owner-gated first request."""
    environ = SpyEnviron({"JUDGE_KEY_A": KEY_A, "JUDGE_KEY_B": KEY_B, "SARVAM_API_KEY": CANARY_KEY})
    resolver = SpyResolver()
    not_listed = [
        "https://evil.example.org/v1/chat/completions",
        "https://api.openai.com/v1/chat/completions/",
        "https://api.openai.com/v1/chat/completion",
        "https://api.openai.com/v1/chat/completions?x=1",
        "https://api.openai.com/v1/chat/completions#frag",
        "https://api.openai.com:8443/v1/chat/completions",
        "http://api.openai.com/v1/chat/completions",
        "https://api.openai.com./v1/chat/completions",
        "https://x.api.openai.com/v1/chat/completions",
        "https://api.openai.com.evil.example/v1/chat/completions",
        "https://user@api.openai.com/v1/chat/completions",
        "https://api.openai.com/v1/../v1/chat/completions",
        "https://api.openai.com",
        "https://8.8.8.8/v1/chat/completions",
        "wss://api.openai.com/v1/chat/completions",
        "",
        "api.openai.com/v1/chat/completions",
    ]
    for url in not_listed:
        assert refusal(
            lambda u=url: bind(u, environ=environ, resolver=resolver, env_file="/nonexistent/env")
        ) in ("host_not_allowed",), url
    # the URL is listed but with another profile
    assert refusal(lambda: bind(URL_A, "xai/1", environ=environ, resolver=resolver)) == (
        "profile_mismatch"
    )
    assert refusal(lambda: bind(URL_B, "openai/1", environ=environ, resolver=resolver)) == (
        "profile_mismatch"
    )
    assert refusal(lambda: bind(URL_A, "generic/1", environ=environ, resolver=resolver)) in (
        "profile_mismatch",
        "unknown_profile",
    )
    # an unverified profile is refused for a real run before the resolver and before the key
    assert refusal(
        lambda: bind(URL_A, environ=environ, resolver=resolver, gate=False), "JudgeProfileError"
    ) == ("profile_unverified")
    assert environ.reads == [], f"the key was looked for: {environ.reads}"
    assert resolver.calls == [], "the name was resolved before the entry was checked"

    # the entry is found whatever the case of the scheme and host, and the default port is implied
    for url in (
        "HTTPS://API.OPENAI.COM/v1/chat/completions",
        "https://api.openai.com:443/v1/chat/completions",
    ):
        bound = bind(url, environ=SpyEnviron({"JUDGE_KEY_A": KEY_A}), resolver=SpyResolver())
        assert bound.endpoint.url == URL_A
    # a match reads exactly the variable the entry names, once the entry and the answers are fine
    environ = SpyEnviron({"JUDGE_KEY_A": KEY_A, "JUDGE_KEY_B": KEY_B})
    resolver = SpyResolver()
    bound = bind(URL_A, environ=environ, resolver=resolver)
    assert resolver.calls == [("api.openai.com", 443)]
    assert set(environ.reads) == {"JUDGE_KEY_A"}
    assert bound.api_key.reveal() == KEY_A
    assert bound.profile.name == "openai/1"
    for form in (repr(bound), str(bound), repr(bound.endpoint)):
        assert KEY_A not in form
        assert KEY_B not in form


def test_judge_key_bound_to_its_host_never_sent_elsewhere(tmp_path: Path) -> None:
    """Rule 14: the key named for one host is sent to that host and to no other; the key variable
    is exactly the entry's (no fall back to another variable); the engine has no URL argument."""
    hosts = jmod("judgehosts")
    compat = jmod("openai_compat")
    environ = {"JUDGE_KEY_A": KEY_A, "JUDGE_KEY_B": KEY_B, "SARVAM_API_KEY": CANARY_KEY}
    sent: dict[str, list[httpx.Request]] = {}
    for name, (url, profile, key) in {
        "a": (URL_A, "openai/1", KEY_A),
        "b": (URL_B, "openrouter/1", KEY_B),
    }.items():
        bound = bind(url, profile, environ=environ)
        transport = ScriptedTransport([openai_reply()])
        log = jmod("simcalllog").SimCallLog.open(tmp_path / name, clock=WallClock())
        engine = compat.create_judge_engine(
            bound,
            call_log=log,
            max_requests=None,
            max_tokens=None,
            transport=transport,
            sleep=Sleeps(),
            monotonic=Ticker(),
        )
        settings = {"provider_slug": "provider-one"} if profile == "openrouter/1" else {}
        outcome = engine.complete(request(0, **settings), context())
        assert outcome.failure is None, name
        sent[name] = transport.requests
        engine.close()
        log.close()
        (made,) = transport.requests
        assert made.headers["authorization"] == f"Bearer {key}"
        assert str(made.url) == url
        other = KEY_B if key == KEY_A else KEY_A
        assert other not in made.headers.get("authorization", "")
        everything = repr(made.headers) + str(made.url) + made.content.decode()
        assert other not in everything
        assert CANARY_KEY not in everything
    assert sent["a"][0].url.host == "api.openai.com"
    assert sent["b"][0].url.host == "openrouter.ai"
    # the engine factory takes the bound endpoint only: no URL, no base URL, no key argument
    parameters = inspect.signature(compat.create_judge_engine).parameters
    assert not {"url", "base_url", "endpoint_url", "api_key", "key"} & set(parameters)
    assert "bound" in parameters

    # the variable named by the entry, no other: a missing one is a refusal naming no value
    judge = core("judge")
    with pytest.raises(judge.JudgeKeyMissingError) as caught:
        bind(
            URL_A,
            environ={"JUDGE_KEY_B": KEY_B, "SARVAM_API_KEY": CANARY_KEY, "OPENAI_API_KEY": KEY_B},
        )
    assert caught.value.reason == "key_missing"
    assert caught.value.__cause__ is None
    for canary in (KEY_A, KEY_B, CANARY_KEY):
        assert canary not in str(caught.value) + repr(caught.value)
    for blank in ("", "   ", "has space", "tab\tin"):
        with pytest.raises(judge.JudgeKeyMissingError):
            bind(URL_A, environ={"JUDGE_KEY_A": blank})

    # one variable name for two hosts would send one key to two hosts: refused in the file
    shared = (
        f"{URL_A} profile=openai/1 key_env=JUDGE_KEY_A\n"
        f"{URL_B} profile=openrouter/1 key_env=JUDGE_KEY_A\n"
    )
    assert refusal(lambda: entries(shared)) == "allowlist_key_env_shared"
    same_host = (
        f"{URL_A} profile=openai/1 key_env=JUDGE_KEY_A\n"
        "https://api.openai.com/v1/other profile=openai/1 key_env=JUDGE_KEY_A\n"
    )
    assert len(entries(same_host)) == 2, "one host, one key, two paths is one binding"

    # the key is read from an env file for that variable only
    env_file = tmp_path / "judge.env"
    env_file.write_text(
        f"OTHER_SECRET_NAME={KEY_B}\nJUDGE_KEY_A={KEY_A}\nSARVAM_API_KEY={CANARY_KEY}\n", "utf-8"
    )
    env_file.chmod(0o600)
    bound = bind(URL_A, environ={}, env_file=env_file)
    assert bound.api_key.reveal() == KEY_A
    twice = tmp_path / "twice.env"
    twice.write_text(f"JUDGE_KEY_A={KEY_A}\nJUDGE_KEY_A={KEY_B}\n", "utf-8")
    twice.chmod(0o600)
    with pytest.raises(judge.JudgeKeyMissingError) as caught:
        bind(URL_A, environ={}, env_file=twice)
    assert caught.value.reason == "key_ambiguous"
    absent = tmp_path / "absent.env"
    absent.write_text(f"OTHER_SECRET_NAME={KEY_B}\n", "utf-8")
    absent.chmod(0o600)
    with pytest.raises(judge.JudgeKeyMissingError) as caught:
        bind(URL_A, environ={"JUDGE_KEY_A": KEY_A}, env_file=absent)
    assert caught.value.reason == "key_missing", (
        "with an env file, the environment is not consulted"
    )
    assert hosts.JudgeAllowlistError is not None


def test_judge_https_only_except_loopback_literal(tmp_path: Path) -> None:
    """Rule 14: https only; ``http`` for a literal loopback address only (never a name such as
    ``localhost``); no userinfo, query or fragment; the loopback profile only on loopback."""
    hosts = jmod("judgehosts")
    ok = "profile=openai/1 key_env=JUDGE_KEY_A"
    loop = "profile=local_loopback/1"
    for line in (
        f"http://127.0.0.1:8123/v1/chat/completions {loop}",
        f"http://127.0.0.2:9/x {loop}",
        f"http://[::1]:8123/v1/chat/completions {loop}",
        f"https://127.0.0.1:8123/v1/chat/completions {loop}",
        f"https://api.openai.com/v1/chat/completions {ok}",
        f"HTTPS://Api.OpenAI.com/v1/chat/completions {ok}",
    ):
        assert len(entries(line)) == 1, line
    assert entries(f"http://127.0.0.1:8123/v1/c {loop}")[0].url == "http://127.0.0.1:8123/v1/c"
    assert entries(f"https://API.openai.COM:443/v1/c {ok}")[0].url == "https://api.openai.com/v1/c"
    assert (
        entries(f"https://api.openai.com:8443/v1/c {ok}")[0].url
        == "https://api.openai.com:8443/v1/c"
    )
    for line, code in (
        (f"http://api.openai.com/v1/chat/completions {ok}", "allowlist_scheme_not_allowed"),
        (f"http://localhost:8123/v1/chat/completions {loop}", "allowlist_scheme_not_allowed"),
        (f"http://127.0.0.1.nip.io/v1/c {loop}", "allowlist_scheme_not_allowed"),
        (f"http://0.0.0.0:8123/v1/c {loop}", "allowlist_scheme_not_allowed"),
        (f"http://127.1/v1/c {loop}", "allowlist_scheme_not_allowed"),
        (f"http://2130706433/v1/c {loop}", "allowlist_scheme_not_allowed"),
        (f"http://10.0.0.5/v1/c {loop}", "allowlist_scheme_not_allowed"),
        (f"ws://127.0.0.1:8123/v1/c {loop}", "allowlist_url_invalid"),
        (f"ftp://api.openai.com/v1/c {ok}", "allowlist_url_invalid"),
        (f"file:///etc/passwd {loop}", "allowlist_url_invalid"),
        (f"https://user:pw@api.openai.com/v1/c {ok}", "allowlist_url_invalid"),
        (f"https://user@api.openai.com/v1/c {ok}", "allowlist_url_invalid"),
        (f"https://api.openai.com/v1/c?key=x {ok}", "allowlist_url_invalid"),
        (f"https://api.openai.com/v1/c?  {ok}", "allowlist_url_invalid"),
        (f"https://api.openai.com/v1/c#frag {ok}", "allowlist_url_invalid"),
        (f"https://api.openai.com {ok}", "allowlist_url_invalid"),
        (f"https://api.openai.com:0/v1/c {ok}", "allowlist_url_invalid"),
        (f"https://api.openai.com:65536/v1/c {ok}", "allowlist_url_invalid"),
        (f"https://api.openai.com:abc/v1/c {ok}", "allowlist_url_invalid"),
        (f"https://api.openai.com/v1/%2e%2e/c {ok}", "allowlist_url_invalid"),
        (f"https://api.openai.com/v1/../c {ok}", "allowlist_url_invalid"),
        (f"https://api.openai.com/v1//c {ok}", "allowlist_url_invalid"),
        (f"https://api openai.com/v1/c {ok}", "allowlist_field_invalid"),
        (f"https://api.openai.com/v1/c {loop}", "allowlist_profile_host_mismatch"),
        (
            "http://127.0.0.1:8123/v1/c profile=local_loopback/1 key_env=JUDGE_KEY_A",
            "allowlist_key_env_forbidden",
        ),
    ):
        assert refusal(lambda t=line: entries(t)) == code, line
    # a real profile on a loopback address is allowed (a user's own server, a scripted fake)
    assert len(entries(f"http://127.0.0.1:8123/v1/c {ok}")) == 1
    # the engine itself refuses a plain http URL that is not a literal loopback address
    compat = jmod("openai_compat")

    log = jmod("simcalllog").SimCallLog.open(tmp_path / "out")

    def make(url: str) -> Any:
        return compat.OpenAICompatJudge(
            None,
            profile=jmod("judgeprofiles").get_profile("local_loopback/1"),
            url=url,
            call_log=log,
            max_requests=None,
            max_tokens=None,
        )

    for url in ("http://api.openai.com/v1/c", "http://localhost:8/v1/c", "http://10.0.0.5/v1/c"):
        with pytest.raises(ValueError, match=r"url"):
            make(url)
    make("http://127.0.0.1:9/v1/c").close()
    make("http://[::1]:9/v1/c").close()
    make("https://api.openai.com/v1/c").close()
    log.close()
    assert hosts.MAX_JUDGE_ALLOWLIST_BYTES == 65_536


def test_judge_client_never_follows_redirects_or_reads_proxy_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rule 14 and A12: ``follow_redirects=False``, ``trust_env=False`` and TLS verified; a
    redirect is a typed failure and the key goes nowhere else; no proxy variable is read."""
    compat = jmod("openai_compat")
    for variable in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "https_proxy", "http_proxy"):
        monkeypatch.setenv(variable, "http://203.0.113.9:3128")
    monkeypatch.setenv("SSL_CERT_FILE", str(tmp_path / "no-such-ca.pem"))
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", str(tmp_path / "no-such-ca.pem"))
    client = compat.build_judge_http_client(None)
    try:
        assert client.follow_redirects is False
        assert client.trust_env is False
        assert client._mounts == {}, "no proxy mount was built from the environment"
        context_ = client._transport._pool._ssl_context
        assert context_.verify_mode == ssl.CERT_REQUIRED
        assert context_.check_hostname is True
    finally:
        client.close()
    # a redirect to another host: one request, a typed failure, the key never leaves
    judge = core("judge")
    for number, code in enumerate((301, 302, 303, 307, 308)):
        redirect = httpx.Response(code, headers={"location": "https://evil.example.org/steal"})
        rig = JudgeRig(tmp_path / f"r{number}", [redirect], profile="openai/1")
        outcome = rig.complete()
        assert isinstance(outcome.failure, judge.JudgeResponseInvalid)
        assert (outcome.failure.reason, outcome.failure.status) == ("unexpected_status", code)
        assert {r.url.host for r in rig.transport.requests} == {"api.openai.com"}
        assert len(rig.transport.requests) == 1
        assert rig.sleeps.waits == []
    # no module of the judge client builds a client by hand, turns the environment on, or turns
    # certificate checking off
    root = Path(inspect.getsourcefile(compat) or "").parent
    for name in ("openai_compat.py", "judgehosts.py", "judgeprofiles.py", "judgeexchange.py"):
        tree = ast.parse((root / name).read_text("utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.keyword):
                if node.arg == "verify":
                    raise AssertionError(f"{name}: a verify argument")
                if node.arg in ("trust_env", "follow_redirects"):
                    assert isinstance(node.value, ast.Constant), name
                    assert node.value.value is False, name
            if isinstance(node, ast.Call):
                called = ast.unparse(node.func)
                assert called not in ("httpx.Client", "httpx.AsyncClient", "requests.get"), (
                    name,
                    called,
                )
    for name in ("openai_compat.py", "judgehosts.py"):
        text = (root / name).read_text("utf-8")
        assert "os.environ" not in text, f"{name} reads the process environment directly"
        assert "getproxies" not in text
        assert "HTTPS_PROXY" not in text


def test_judge_dns_answer_private_refused_for_public_entry() -> None:
    """Rule 14: every address a name resolves to must be a public one; a private, loopback,
    link-local or otherwise non-public answer (v4 or v6, mapped or not, in any position of the
    answer) refuses the entry before the key is read. A residual rebinding race is not claimed
    closed."""
    bad_answers = [
        ["10.0.0.5"],
        ["172.16.0.1"],
        ["192.168.1.10"],
        ["127.0.0.1"],
        ["127.1.2.3"],
        ["169.254.169.254"],
        ["0.0.0.0"],  # noqa: S104
        ["::1"],
        ["fe80::1"],
        ["fe80::1%eth0"],
        ["fd00::1"],
        ["fc00::1"],
        ["::"],
        ["::ffff:10.0.0.1"],
        ["::ffff:127.0.0.1"],
        ["224.0.0.1"],
        ["8.8.8.8", "10.0.0.1"],
        ["10.0.0.1", "8.8.8.8"],
        ["8.8.8.8", "2606:4700:4700::1111", "fd00::1"],
        ["not an address"],
    ]
    for answer in bad_answers:
        environ = SpyEnviron({"JUDGE_KEY_A": KEY_A})
        resolver = SpyResolver({"api.openai.com": answer})
        code = refusal(lambda e=environ, r=resolver: bind(URL_A, environ=e, resolver=r))
        assert code == "dns_answer_not_public", answer
        assert environ.reads == [], f"the key was read after a bad answer {answer}"
        assert resolver.calls == [("api.openai.com", 443)]
    # no answer and a failing resolver are refusals too, never a fall back to "allow"
    environ = SpyEnviron({"JUDGE_KEY_A": KEY_A})
    assert refusal(
        lambda: bind(URL_A, environ=environ, resolver=SpyResolver({"api.openai.com": []}))
    ) == ("dns_no_answer")

    def fails(host: str, port: int) -> list[str]:
        raise OSError("name or service not known")

    assert refusal(lambda: bind(URL_A, environ=environ, resolver=fails)) == "dns_failed"
    assert environ.reads == []
    # public answers pass (v4 and v6), the port reaches the resolver
    for answer in (["8.8.8.8"], ["2606:4700:4700::1111"], ["1.1.1.1", "2606:4700:4700::1111"]):
        resolver = SpyResolver({"openrouter.ai": answer})
        bound = bind(URL_B, "openrouter/1", resolver=resolver)
        assert bound.api_key.reveal() == KEY_B
        assert resolver.calls == [("openrouter.ai", 443)]
    custom = "https://gateway.example.org:8443/v1/c profile=openai/1 key_env=JUDGE_KEY_A\n"
    resolver = SpyResolver({"gateway.example.org": ["8.8.8.8"]})
    bind("https://gateway.example.org:8443/v1/c", text=custom, resolver=resolver)
    assert resolver.calls == [("gateway.example.org", 8443)]
    # a literal address is its own answer: a private literal is refused, a public one passes,
    # and the resolver is not called for either; a literal loopback entry skips the check
    literal_private = "https://10.0.0.5/v1/c profile=openai/1 key_env=JUDGE_KEY_A\n"
    resolver = SpyResolver()
    environ = SpyEnviron({"JUDGE_KEY_A": KEY_A})
    assert (
        refusal(
            lambda: bind(
                "https://10.0.0.5/v1/c", text=literal_private, environ=environ, resolver=resolver
            )
        )
        == "dns_answer_not_public"
    )
    assert resolver.calls == []
    assert environ.reads == []
    literal_public = "https://8.8.8.8/v1/c profile=openai/1 key_env=JUDGE_KEY_A\n"
    bind("https://8.8.8.8/v1/c", text=literal_public, resolver=resolver)
    assert resolver.calls == []
    resolver = SpyResolver({"127.0.0.1": ["10.0.0.1"]})
    loop = bind(URL_LOOP, "local_loopback/1", gate=False, resolver=resolver)
    assert loop.api_key is None
    assert resolver.calls == []
    # the name 'localhost' as a public entry resolves to loopback and is refused
    named = "https://localhost:8443/v1/c profile=openai/1 key_env=JUDGE_KEY_A\n"
    resolver = SpyResolver({"localhost": ["127.0.0.1", "::1"]})
    assert refusal(lambda: bind("https://localhost:8443/v1/c", text=named, resolver=resolver)) == (
        "dns_answer_not_public"
    )


def test_judge_dns_default_resolver_is_the_real_one() -> None:
    """Without an injected resolver the entry's name is resolved by the system resolver; under the
    no-network guard of the tests that raises ``AssertionError``, which is not swallowed."""
    hosts = jmod("judgehosts")
    assert hosts.system_resolver.__module__ == hosts.__name__
    with pytest.raises(AssertionError, match="network"):
        jmod("judgehosts").bind_endpoint(
            entries(),
            url=URL_A,
            profile="openai/1",
            environ={"JUDGE_KEY_A": KEY_A},
            first_request_gate=True,
        )


def test_judge_allowlist_mode_and_link_rules(tmp_path: Path) -> None:
    """The allowlist is a private regular file read without following a link; an unknown or
    repeated field, a repeated entry or an oversized file refuses the whole file."""
    hosts = jmod("judgehosts")
    good = tmp_path / "judge-allowlist"
    good.write_text(ALLOWLIST, "utf-8")
    good.chmod(0o600)
    assert [e.url for e in hosts.read_judge_allowlist(good)] == [URL_A, URL_B, URL_LOOP]
    first = hosts.read_judge_allowlist(good)[0]
    assert (first.scheme, first.host, first.port, first.path) == (
        "https",
        "api.openai.com",
        443,
        "/v1/chat/completions",
    )
    assert (first.profile, first.key_env) == ("openai/1", "JUDGE_KEY_A")
    loop = hosts.read_judge_allowlist(good)[2]
    assert (loop.profile, loop.key_env, loop.port) == ("local_loopback/1", None, 8123)

    for mode in (0o640, 0o644, 0o660, 0o666, 0o604, 0o602, 0o610):
        good.chmod(mode)
        assert refusal(lambda: hosts.read_judge_allowlist(good)) == "allowlist_mode_too_open", oct(
            mode
        )
    for mode in (0o600, 0o400):
        good.chmod(mode)
        assert len(hosts.read_judge_allowlist(good)) == 3
    good.chmod(0o600)

    link = tmp_path / "link"
    link.symlink_to(good)
    assert refusal(lambda: hosts.read_judge_allowlist(link)) == "allowlist_not_a_regular_file"
    hard = tmp_path / "hard"
    os.link(good, hard)
    assert refusal(lambda: hosts.read_judge_allowlist(hard)) == "allowlist_not_a_regular_file"
    assert refusal(lambda: hosts.read_judge_allowlist(good)) == "allowlist_not_a_regular_file"
    hard.unlink()
    assert len(hosts.read_judge_allowlist(good)) == 3
    assert refusal(lambda: hosts.read_judge_allowlist(tmp_path)) == "allowlist_not_a_regular_file"
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo, 0o600)
    assert refusal(lambda: hosts.read_judge_allowlist(fifo)) == "allowlist_not_a_regular_file"
    assert (
        refusal(lambda: hosts.read_judge_allowlist(tmp_path / "missing")) == "allowlist_unreadable"
    )
    big = tmp_path / "big"
    big.write_text("# " + "x" * 65_536 + "\n", "utf-8")
    big.chmod(0o600)
    assert refusal(lambda: hosts.read_judge_allowlist(big)) == "allowlist_too_large"
    odd = tmp_path / "odd"
    odd.write_bytes(b"\xff\xfe\x00")
    odd.chmod(0o600)
    assert refusal(lambda: hosts.read_judge_allowlist(odd)) == "allowlist_unreadable"

    ok = f"{URL_A} profile=openai/1 key_env=JUDGE_KEY_A"
    bad_files = [
        (f"{ok} extra=1", "allowlist_field_invalid"),
        (f"{ok} profile=openai/1", "allowlist_field_invalid"),
        (f"{ok} key_env=JUDGE_KEY_A", "allowlist_field_invalid"),
        (f"{ok} stray", "allowlist_field_invalid"),
        (f"{ok} =x", "allowlist_field_invalid"),
        (f"{URL_A} profile= key_env=JUDGE_KEY_A", "allowlist_field_invalid"),
        (f"{URL_A} profile=openai/1", "allowlist_field_missing"),
        (f"{URL_A} key_env=JUDGE_KEY_A", "allowlist_field_missing"),
        (f"{URL_A} profile=generic/1 key_env=JUDGE_KEY_A", "allowlist_profile_unknown"),
        (f"{URL_A} profile=fake/1 key_env=JUDGE_KEY_A", "allowlist_profile_unknown"),
        (f"{ok}\n{ok}", "allowlist_entry_repeated"),
        (
            f"{ok}\nHTTPS://API.OPENAI.COM:443/v1/chat/completions "
            "profile=openai/1 key_env=JUDGE_KEY_B",
            "allowlist_entry_repeated",
        ),
    ]
    for text, code in bad_files:
        assert refusal(lambda t=text: entries(t)) == code, text
    assert entries("\n\n# nothing\n   \n") == ()
    assert len(entries(f"{ok}  # comment with profile=xai/1 and a key " + "sk-" + "a" * 24)) == 1
    # a comment starts at '#' only after white space: a '#' inside a URL is a fragment
    assert refusal(
        lambda: entries("https://api.openai.com/v1/c#x profile=openai/1 key_env=JUDGE_KEY_A")
    ) == ("allowlist_url_invalid")


def test_judge_key_env_name_pattern_and_never_value_in_config() -> None:
    """Rule 15: ``key_env`` is a variable NAME (upper-case letters, digits, underscore, 1 to 64
    characters, starting with a letter); a value, or a name that has the shape of a key, is
    refused, and nothing a bound endpoint shows holds a key value."""
    hosts = jmod("judgehosts")
    assert hosts.KEY_ENV_PATTERN.fullmatch("JUDGE_KEY_A")
    template = "https://api.openai.com/v1/chat/completions profile=openai/1 key_env={}"
    for name in ("A", "OPENAI_API_KEY", "JUDGE_KEY_1", "X" * 64, "SARVAM_API_KEY"):
        assert entries(template.format(name))[0].key_env == name, name
    for name in (
        "openai_api_key",
        "My_Key",
        "1KEY",
        "_KEY",
        "MY-KEY",
        "MY KEY",
        "X" * 65,
        "sk-" + "a" * 40,
        "AKIA" + "IOSFODNN7" + "EXAMPLE",
        KEY_A,
        CANARY_KEY,
        "KEY=value",
        "KEY\u0661",
    ):
        line = template.format(name)
        assert refusal(lambda t=line: entries(t)) in (
            "allowlist_key_env_invalid",
            "allowlist_field_invalid",
        ), name
    # a key-shaped stray token anywhere on the line is refused as a field, never kept
    leaked = template.format("JUDGE_KEY_A") + " " + "sk-" + "b" * 40
    assert refusal(lambda: entries(leaked)) == "allowlist_field_invalid"
    # the profile document records the NAME only, and refuses a value
    records = core("judgerecords")
    golden = core("golden").parse_golden_set(
        json.dumps(
            {
                "format": "tracer.golden_set/1",
                "key": "set_one",
                "language": "mr",
                "items": [
                    {
                        "id": "q1",
                        "scripted_turn_index": 0,
                        "question": "\u0905",
                        "match_mode": "equivalent_answer",
                        "expected_answer": "\u0905",
                        "needs_native_review": True,
                    }
                ],
            },
            ensure_ascii=False,
        )
    )

    def profile_document(key_env: str | None) -> dict[str, Any]:
        built: dict[str, Any] = records.build_judge_profile_document(
            profile="openai/1",
            host="api.openai.com",
            path="/v1/chat/completions",
            model="judge-model-1",
            params={"max_completion_tokens": 256},
            max_judge_requests=10,
            max_judge_tokens=5000,
            key_env=key_env,
            golden=golden,
        )
        return built

    assert profile_document("JUDGE_KEY_A")["key_env"] == "JUDGE_KEY_A"
    for value in (KEY_A, "sk-" + "c" * 40, "lower_case"):
        with pytest.raises(records.JudgeDocumentError) as caught:
            profile_document(value)
        assert value not in str(caught.value) + repr(caught.value)
    # an endpoint shows its name and never its value
    bound = bind(URL_A)
    shown = repr(bound) + str(bound) + repr(bound.endpoint) + repr(vars(bound.endpoint))
    assert "JUDGE_KEY_A" in repr(bound.endpoint)
    assert KEY_A not in shown
    with pytest.raises(TypeError):
        pickle.dumps(bound.api_key)
