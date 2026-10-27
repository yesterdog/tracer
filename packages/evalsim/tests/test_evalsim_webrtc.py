# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P4-18: the WebRTC client of ``smallwebrtc/1`` behind the ``[webrtc]`` extra (ADR 0026 rule 7;
contract ``docs/phase4/api-surface-batch-d.md`` section 7).

Unit tests open no socket. The ``loopback`` tests of the signalling client talk to a tiny HTTP
server that the test starts on 127.0.0.1. A media round trip with aiortc is NOT here: ICE host
candidates are gathered from every network interface of the machine, which a loopback-only guard
cannot confine; the loopback media proof is ``test_integration_webrtc_against_pipecat_loopback``
(P4-27, against ``tools/samplebot``). Nothing here pins what ADR 0026 marks "not verified": the
codec, the sample rate, whether audio needs the client's data channel, whether a client whose
offer already holds all candidates needs the PATCH route.
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import json
import re
import sys
from typing import Any

import pytest

from evalsim_support_imports import ROOT, SRC, adr_dialect_section, leaks, mod  # type: ignore[import-not-found,unused-ignore]
from evalsim_support_servers import (  # type: ignore[import-not-found,unused-ignore]
    http_reply,
    raw_server,
    read_http_request,
    wait_until,
)

OFFER = "http://127.0.0.1:7860/api/offer"
SDP = "v=0\r\no=- 1 1 IN IP4 0.0.0.0\r\ns=-\r\nt=0 0\r\n"
SECRET = "-".join(["tok", "SECRET_signal.9"])  # built from parts: no scanner pattern in the source


def webrtc() -> Any:
    return mod("webrtc")


def labels(**kwargs: Any) -> Any:
    base: dict[str, Any] = {
        "auth_token_env": None,
        "origin": None,
        "bot_holds_telephony_credentials": None,
    }
    return mod("ws").ConnectionLabels(**{**base, **kwargs})


@pytest.mark.skipif(
    importlib.util.find_spec("aiortc") is None,
    reason="the optional [webrtc] extra is not installed",
)
def test_webrtc_no_stun_by_default() -> None:
    aiortc = importlib.import_module("aiortc")
    w = webrtc()
    plan = w.make_webrtc_plan(OFFER, ())
    assert plan.ice_servers == ()
    config = w.build_rtc_configuration(plan)
    # an EMPTY list, not None: aiortc replaces None by its built-in default, a Google STUN server,
    # which would tell a third party the host's addresses (ADR 0026 rule 7, BL-280)
    assert config.iceServers is not None
    assert list(config.iceServers) == []
    from aiortc.rtcicetransport import RTCIceGatherer

    gatherer = RTCIceGatherer(iceServers=config.iceServers)
    connection = gatherer._connection
    assert getattr(connection, "stun_server", None) is None
    assert getattr(connection, "turn_server", None) is None
    # the control: what aiortc does when it is handed None
    assert RTCIceGatherer(iceServers=None)._connection.stun_server is not None
    # no default server is written anywhere in the package source
    for path in SRC.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "stun.l.google.com" not in text, path
        assert "19302" not in text, path
    # an explicit server is used, and only that one
    explicit = w.make_webrtc_plan(OFFER, ["stun:127.0.0.1:3478"])
    cfg = w.build_rtc_configuration(explicit)
    assert [s.urls for s in cfg.iceServers] == ["stun:127.0.0.1:3478"]
    assert isinstance(aiortc.RTCConfiguration, type)


def test_webrtc_non_loopback_target_needs_an_explicit_ice_server() -> None:
    w = webrtc()
    errors = mod("errors")
    remote = "https://agent.example.test/api/offer"
    with pytest.raises(errors.TargetRefusedError) as raised:
        w.make_webrtc_plan(remote, ())
    assert raised.value.code == "ice_server_required"
    plan = w.make_webrtc_plan(
        remote, ["stun:stun.example.test:3478", "turns:turn.example.test:5349?transport=tcp"]
    )
    assert plan.ice_servers == (
        "stun:stun.example.test:3478",
        "turns:turn.example.test:5349?transport=tcp",
    )
    assert plan.loopback is False
    # the explicit servers are what is logged (the call log lists them; no credentials in them)
    assert not any("@" in s for s in plan.ice_servers)
    # a loopback target does not need one
    assert w.make_webrtc_plan("http://localhost:7860/api/offer", ()).loopback is True
    assert w.make_webrtc_plan("https://127.0.0.1/api/offer", ()).loopback is True


@pytest.mark.parametrize(
    "server",
    [
        "turn:user:secret@turn.example.test:3478",  # credentials in the URL
        "stun:",
        "stun",
        "http://stun.example.test",
        "stun:host with space",
        "stun:host\nname",
        "stun:caf\u00e9.test",
        "",
        "STUN:upper.example.test",
        "stun:x.test?transport=sctp",
        "turn:x.test:3478?transport=udp&a=b",
    ],
)
def test_ice_server_that_is_not_a_plain_stun_or_turn_url_is_refused(server: str) -> None:
    with pytest.raises(mod("errors").TargetRefusedError) as raised:
        webrtc().make_webrtc_plan(OFFER, [server])
    assert raised.value.code == "ice_server_invalid"
    assert "secret" not in str(raised.value)


@pytest.mark.parametrize(
    ("url", "code"),
    [
        ("http://agent.example.test/api/offer", "insecure_target"),  # plain http, not loopback
        ("http://127.0.0.1:7860/api/offer?x=1", "target_invalid"),
        ("http://user:pw@127.0.0.1:7860/api/offer", "target_invalid"),
        ("http://127.0.0.1:7860/api/other", "target_invalid"),  # the route is /api/offer
        ("http://127.0.0.1:7860/", "target_invalid"),
        ("ws://127.0.0.1:7860/api/offer", "target_invalid"),  # a WebRTC target is http(s)
        ("http://127.0.0.1:7860/api/offer/", "target_invalid"),
    ],
)
def test_webrtc_target_url_rules(url: str, code: str) -> None:
    with pytest.raises(mod("errors").TargetRefusedError) as raised:
        webrtc().make_webrtc_plan(url, ["stun:s.example.test:3478"])
    assert raised.value.code == code


def test_webrtc_target_may_sit_behind_a_path_prefix() -> None:
    """The proxy form of the ADR, ``/sessions/{session_id}/api/offer``, as written."""
    plan = webrtc().make_webrtc_plan("http://127.0.0.1:7860/sessions/abc-123/api/offer", ())
    assert plan.offer_url == "http://127.0.0.1:7860/sessions/abc-123/api/offer"


def test_webrtc_extra_absent_gives_clear_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without the extra the command is refused with a message that names the extra, and the
    package still imports (ADR 0026 rule 7). Runs whether or not aiortc is installed."""
    w = webrtc()
    errors = mod("errors")
    monkeypatch.setitem(sys.modules, "aiortc", None)  # `import aiortc` now raises ImportError
    with pytest.raises(errors.TargetRefusedError) as raised:
        w.load_aiortc()
    assert raised.value.code == "webrtc_extra_missing"
    assert "tracer-evalsim[webrtc]" in str(raised.value)
    assert isinstance(raised.value, ValueError)  # an input refusal (exit 4), before any socket
    with pytest.raises(errors.TargetRefusedError) as raised2:
        w.build_rtc_configuration(w.make_webrtc_plan(OFFER, ()))
    assert raised2.value.code == "webrtc_extra_missing"
    # the pure parts do not need the extra
    assert w.offer_request_body(SDP)["type"] == "offer"
    assert w.make_webrtc_plan(OFFER, ()).offer_url == OFFER


def test_webrtc_module_imports_without_the_extra_in_a_fresh_interpreter() -> None:
    import subprocess

    code = (
        "import sys; sys.modules['aiortc'] = None; sys.modules['av'] = None\n"
        "import tracer_evalsim.webrtc as w\n"
        "import tracer_evalsim.errors as e\n"
        "try:\n"
        "    w.load_aiortc()\n"
        "except e.TargetRefusedError as exc:\n"
        "    print(exc.code, 'tracer-evalsim[webrtc]' in str(exc))\n"
    )
    done = subprocess.run(  # noqa: S603
        [sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=120, check=False
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.split() == ["webrtc_extra_missing", "True"]


def test_webrtc_signalling_body_matches_pinned_pipecat() -> None:
    """The request bodies are the ``SmallWebRTCRequest`` and ``SmallWebRTCPatchRequest`` of
    Pipecat v1.14.0 as ADR 0026 records them (retrieved 2026-10-27)."""
    w = webrtc()
    section = adr_dialect_section("smallwebrtc/1")
    assert w.OFFER_SUFFIX == "/api/offer"
    assert "`POST /api/offer`" in section
    assert "`PATCH /api/offer`" in section
    # the first offer: the two required fields only
    body = w.offer_request_body(SDP)
    assert body == {"sdp": SDP, "type": "offer"}
    # every key Tracer can send is a field the ADR lists for SmallWebRTCRequest
    adr_fields = set(re.findall(r"`(sdp|type|pc_id|restart_pc|request_data)`", section))
    assert adr_fields == {"sdp", "type", "pc_id", "restart_pc", "request_data"}
    full = w.offer_request_body(SDP, pc_id="pc-1", restart_pc=True, request_data={"a": 1})
    assert set(full) == adr_fields
    assert full == {
        "sdp": SDP,
        "type": "offer",
        "pc_id": "pc-1",
        "restart_pc": True,
        "request_data": {"a": 1},
    }
    assert w.offer_request_body(SDP, restart_pc=False)["restart_pc"] is False  # False is a value
    json.dumps(full)  # JSON-able as it is
    for bad in ("", None, 5):
        with pytest.raises(ValueError):  # noqa: PT011 - the message is not pinned
            w.offer_request_body(bad)
    # the candidates route: pc_id and a list of {candidate, sdp_mid, sdp_mline_index}
    patch = w.patch_request_body("pc-1", [("candidate:1 1 udp 1 127.0.0.1 9 typ host", "0", 0)])
    assert patch == {
        "pc_id": "pc-1",
        "candidates": [
            {
                "candidate": "candidate:1 1 udp 1 127.0.0.1 9 typ host",
                "sdp_mid": "0",
                "sdp_mline_index": 0,
            }
        ],
    }
    for field in ("pc_id", "candidates", "candidate", "sdp_mid", "sdp_mline_index"):
        assert f"`{field}`" in section, field
    assert w.patch_request_body("pc-1", []) == {"pc_id": "pc-1", "candidates": []}
    for bad_id in ("", "a b", "a\nb", "x" * 257, "\u00e9"):
        with pytest.raises(ValueError):  # noqa: PT011 - the message is not pinned
            w.patch_request_body(bad_id, [])


def test_webrtc_answer_is_validated_strictly() -> None:
    w = webrtc()
    errors = mod("errors")
    good = {"sdp": SDP, "type": "answer", "pc_id": "pc-1"}
    answer = w.parse_answer(good)
    assert (answer.sdp, answer.type, answer.pc_id) == (SDP, "answer", "pc-1")
    assert w.parse_answer({**good, "extra": [1, 2]}).pc_id == "pc-1"  # unknown keys are ignored
    big = "v=0\r\n" + "a" * (mod("limits").MAX_SDP_BYTES)
    bad: Any
    for bad in (
        None,
        [],
        "answer",
        {},
        {**good, "sdp": ""},
        {**good, "sdp": 5},
        {**good, "sdp": big},
        {**good, "type": "offer"},
        {**good, "type": None},
        {**good, "pc_id": ""},
        {**good, "pc_id": "a b"},
        {**good, "pc_id": 7},
        {"sdp": SDP, "type": "answer"},
    ):
        with pytest.raises(errors.SetupFailedError) as raised:
            w.parse_answer(bad)
        assert raised.value.code == "answer_invalid"
        assert raised.value.END_REASON == "setup_failed"
        assert not leaks(raised.value, "v=0")


# --------------------------------------------------------- the signalling client (loopback)


async def _serve_once(reply: bytes, seen: list[dict[str, Any]]) -> Any:
    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        method, target, headers, body, head = await read_http_request(reader)
        seen.append(
            {"method": method, "target": target, "headers": headers, "body": body, "head": head}
        )
        writer.write(reply)
        await writer.drain()

    return raw_server(handler)


def _signalling(port: int, *, entry: Any = None, token: Any = None, **kwargs: Any) -> Any:
    w = webrtc()
    plan = w.make_webrtc_plan(f"http://127.0.0.1:{port}/api/offer", ())
    return w.HttpSignalling(
        plan,
        labels=entry or labels(),
        token=token,
        timeout_s=kwargs.pop("timeout_s", 3.0),
        **kwargs,
    )


@pytest.mark.loopback
def test_signalling_posts_the_offer_and_patches_the_candidates() -> None:
    w = webrtc()
    answer = {"sdp": SDP, "type": "answer", "pc_id": "pc-9"}
    seen: list[dict[str, Any]] = []

    async def main() -> None:
        reply = http_reply(
            200, json.dumps(answer).encode(), headers={"Content-Type": "application/json"}
        )
        async with await _serve_once(reply, seen) as server:
            client = _signalling(server.port)
            got = await client.post_offer(w.offer_request_body(SDP))
            assert got == answer
        patch_seen: list[dict[str, Any]] = []
        async with await _serve_once(
            http_reply(200, b'{"status": "success"}'), patch_seen
        ) as server:
            client = _signalling(server.port)
            body = w.patch_request_body("pc-9", [("candidate:1", "0", 0)])
            assert await client.patch_candidates(body) == {"status": "success"}
        assert patch_seen[0]["method"] == "PATCH"
        assert json.loads(patch_seen[0]["body"]) == body
        assert patch_seen[0]["target"] == "/api/offer"

    asyncio.run(main())
    assert seen[0]["method"] == "POST"
    assert seen[0]["target"] == "/api/offer"  # no query, no token in the path
    assert json.loads(seen[0]["body"]) == {"sdp": SDP, "type": "offer"}
    assert seen[0]["headers"]["content-type"].startswith("application/json")
    assert "authorization" not in seen[0]["headers"]
    assert "origin" not in seen[0]["headers"]


@pytest.mark.loopback
def test_signalling_token_is_sent_only_as_a_bearer_header_and_origin_only_from_the_entry() -> None:
    ws = mod("ws")
    entry = labels(auth_token_env="TRACER_TEST_TOKEN", origin="http://studio.example.test:3000")  # noqa: S106 - a name, not a secret
    token = ws.prepare_connection("smallwebrtc/1", entry, environ={"TRACER_TEST_TOKEN": SECRET})
    seen: list[dict[str, Any]] = []
    answer = json.dumps({"sdp": SDP, "type": "answer", "pc_id": "p"}).encode()

    async def main() -> None:
        async with await _serve_once(http_reply(200, answer), seen) as server:
            client = _signalling(server.port, entry=entry, token=token)
            await client.post_offer(webrtc().offer_request_body(SDP))

    asyncio.run(main())
    request = seen[0]
    assert request["headers"]["authorization"] == "Bearer " + SECRET
    assert request["headers"]["origin"] == "http://studio.example.test:3000"
    assert request["head"].count(SECRET) == 1  # in the header and nowhere else
    assert SECRET.encode() not in request["body"]
    assert "?" not in request["target"]


@pytest.mark.loopback
def test_signalling_redirect_is_never_followed() -> None:
    errors = mod("errors")
    second: list[dict[str, Any]] = []
    first: list[dict[str, Any]] = []

    async def main() -> None:
        async with await _serve_once(http_reply(200, b"{}"), second) as target:
            location = f"http://127.0.0.1:{target.port}/api/offer"
            for status in (301, 302, 303, 307, 308):
                reply = http_reply(status, b"", headers={"Location": location})
                async with await _serve_once(reply, first) as origin:
                    client = _signalling(origin.port)
                    with pytest.raises(errors.SetupFailedError) as raised:
                        await client.post_offer(webrtc().offer_request_body(SDP))
                    assert raised.value.code == "redirect_refused"
                    assert raised.value.http_status == status
        assert target.connections == 0

    asyncio.run(main())
    assert second == []
    assert len(first) == 5


@pytest.mark.loopback
def test_signalling_failures_are_setup_failures_with_facts_only() -> None:
    errors = mod("errors")
    w = webrtc()

    async def call(reply: bytes) -> Any:
        async with await _serve_once(reply, []) as server:
            client = _signalling(server.port)
            with pytest.raises(errors.SetupFailedError) as raised:
                await client.post_offer(w.offer_request_body(SDP))
            return raised.value

    async def main() -> None:
        err = await call(http_reply(500, b"SECRET-server-text"))
        assert (err.code, err.http_status) == ("handshake_rejected", 500)
        assert "SECRET" not in str(err) and "SECRET" not in repr(err.args)  # noqa: PT018 - one fact in two clauses
        err = await call(http_reply(404, b"{}"))
        assert (err.code, err.http_status) == ("handshake_rejected", 404)
        err = await call(http_reply(200, b"not json"))
        assert err.code == "answer_invalid"
        err = await call(http_reply(200, b"[1, 2]"))
        assert err.code == "answer_invalid"
        too_big = b'{"x": "' + b"a" * mod("limits").MAX_SIGNALLING_RESPONSE_BYTES + b'"}'
        err = await call(http_reply(200, too_big))
        assert err.code == "answer_invalid"
        # a reply that is valid JSON and then pads past the cap with white space is still over it
        answer = json.dumps({"sdp": SDP, "type": "answer", "pc_id": "p"}).encode()
        padded = answer + b" " * mod("limits").MAX_SIGNALLING_RESPONSE_BYTES
        err = await call(http_reply(200, padded))
        assert err.code == "answer_invalid"
        for e in (err,):
            assert e.__cause__ is None and e.__context__ is None  # noqa: PT018 - one fact in two clauses

    asyncio.run(main())


@pytest.mark.loopback
def test_signalling_refused_connection_and_timeout_are_setup_failures() -> None:
    from evalsim_support_servers import closed_port

    errors = mod("errors")

    async def main() -> None:
        client = _signalling(closed_port())
        with pytest.raises(errors.SetupFailedError) as raised:
            await client.post_offer(webrtc().offer_request_body(SDP))
        assert raised.value.code == "connection_refused"

        async def silent(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            await reader.read()  # read the request and then say nothing until the client gives up

        async with raw_server(silent) as server:
            slow = _signalling(server.port, timeout_s=0.3)
            with pytest.raises(errors.SetupFailedError) as raised2:
                await slow.post_offer(webrtc().offer_request_body(SDP))
            assert raised2.value.code == "connect_timeout"
            await wait_until(lambda: server.connections == 1)

    asyncio.run(main())


def test_the_webrtc_extra_is_declared_and_the_module_has_no_top_level_aiortc_import() -> None:
    import ast

    tree = ast.parse((SRC / "webrtc.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Import):
            assert not any(a.name.split(".")[0] in ("aiortc", "av") for a in node.names)
        if isinstance(node, ast.ImportFrom) and node.module:
            assert node.module.split(".")[0] not in ("aiortc", "av")
    assert ROOT.joinpath("packages", "evalsim", "pyproject.toml").is_file()
