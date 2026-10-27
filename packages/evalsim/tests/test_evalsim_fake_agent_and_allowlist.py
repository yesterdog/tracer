# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P4-19 / P4-21 / P4-31 (what step 2 needs): the offline fake agent of ``--agent fake:<scenario>``
and the allowlist reader. Open no socket. Contract: docs/phase4/api-surface-batch-e.md sections 3.3
and 3.4.

The fake agent is a product module (the offline run of milestone M1 uses it); it is deterministic on
the injected clock and speaks the dialect of the run to the simulator over an in-memory connection.
The allowlist tests here pin only what the command needs at step 2 (grammar, normalisation, exact
match, the file rules); the SCOPE-named allowlist tests of P4-31 belong to batch G.
"""

from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
from typing import Any

import pytest

from evalsim_support_clock import ManualClock, run_virtual  # type: ignore[import-not-found,unused-ignore]
from evalsim_support_runner import dialect_of, mod  # type: ignore[import-not-found,unused-ignore]


def scenario(*steps: Any) -> str:
    return json.dumps({"format": "tracer.fake_agent/1", "steps": list(steps)})


def fa() -> Any:
    return mod("fakeagent")


GOOD = scenario(
    {"wait_ms": 100},
    {"speak": {"frames": 3, "level": 1000, "text": "one"}},
    {"silence": {"frames": 2}},
    {"comfort": {"frames": 2}},
    {"garbage": {"size": 5, "seed": 1}},
    {"speak": {"frames": 1, "level": 500, "text": "two"}},
    {"hang_up": 1001},
)


def test_a_scenario_parses_to_typed_steps_and_gives_its_words_in_order() -> None:
    parsed = fa().parse_fake_agent(GOOD)
    assert [s.kind for s in parsed.steps] == [
        "wait",
        "speak",
        "silence",
        "comfort",
        "garbage",
        "speak",
        "hang_up",
    ]
    assert fa().speech_texts(parsed) == ("one", "two")
    assert parsed.steps[-1].code == 1001
    assert fa().FAKE_AGENT_FORMAT == "tracer.fake_agent/1"


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        '{"format": "tracer.fake_agent/1"}',
        '{"format": "x", "steps": []}',
        scenario({"wait_ms": -1}),
        scenario({"wait_ms": True}),
        scenario({"speak": {"frames": 0, "level": 1}}),
        scenario({"speak": {"frames": 1, "level": 0}}),
        scenario({"speak": {"frames": 1, "level": 1, "text": ""}}),
        scenario({"speak": {"frames": 1, "level": 1, "x": 1}}),
        scenario({"silence": {}}),
        scenario({"hang_up": 999}),
        scenario({"abort": False}),
        scenario({"dance": 1}),
        scenario({"wait_ms": 1, "abort": True}),
        json.dumps({"format": "tracer.fake_agent/1", "steps": [{"wait_ms": 1}] * 1001}),
    ],
)
def test_a_bad_scenario_is_refused_with_a_code(text: str) -> None:
    with pytest.raises(fa().FakeAgentError) as caught:
        fa().parse_fake_agent(text)
    assert str(caught.value) == caught.value.code
    assert caught.value.code.startswith("fake_agent_")


def test_speech_without_text_has_no_words() -> None:
    parsed = fa().parse_fake_agent(scenario({"speak": {"frames": 1, "level": 5}}))
    with pytest.raises(fa().FakeAgentError) as caught:
        fa().speech_texts(parsed)
    assert caught.value.code == "fake_agent_speech_without_text"


def listen(kind: str, text: str) -> list[tuple[int, Any]]:
    """Connect to the fake agent and read what it sends: (ms after connect, decoded event)."""
    dialect = dialect_of(kind)
    if kind == "twilio":
        dialect.opening_messages()
    clock = ManualClock()
    seen: list[tuple[int, Any]] = []
    reader = mod("dialects.base").InboundReader(dialect, clock)

    async def main() -> None:
        connection = await fa().connect_fake_agent(fa().parse_fake_agent(text), dialect, clock)()
        origin = clock.monotonic_ns()
        while (message := await connection.recv()) is not None:
            event = reader.feed(message)
            seen.append(((clock.monotonic_ns() - origin) // 1_000_000, event))
        seen.append(((clock.monotonic_ns() - origin) // 1_000_000, connection.close_info))
        await connection.close()

    run_virtual(clock, main)
    return seen


@pytest.mark.parametrize("kind", ["protobuf", "twilio"])
def test_the_fake_agent_speaks_the_dialect_on_the_clock_and_hangs_up(kind: str) -> None:
    """Speech frames at 100, 120, 140; digital silence and comfort noise are audio too (the
    simulator, not the agent, decides what is active); garbage is ignored by the dialect; the
    hang-up carries its code. Frames are 20 ms apart: a step ends when its last frame ends."""
    seen = listen(kind, GOOD)
    audio = [(t, e) for t, e in seen if e is not None and getattr(e, "kind", "") == "audio"]
    assert [t for t, _ in audio] == [100, 120, 140, 160, 180, 200, 220, 240]
    payloads = [e.audio for _, e in audio]
    rate = dialect_of(kind).frame_bytes
    assert all(len(p) == rate for p in payloads)
    codec = dialect_of(kind).wire_codec
    pcm = [
        importlib.import_module("tracer_evalcore.recorder").decode_to_pcm16(codec, p)
        for p in payloads
    ]
    assert any(pcm[0])
    assert not any(pcm[3])
    assert any(pcm[5])
    close = seen[-1]
    assert close[1].code == 1001
    assert close[1].clean
    assert close[0] == 260
    assert sum(1 for _, e in seen[:-1] if e is None) == 1  # the garbage message, ignored


def test_the_fake_agent_is_deterministic_and_aborts_without_a_close_frame() -> None:
    assert listen("protobuf", GOOD) == listen("protobuf", GOOD)
    seen = listen("protobuf", scenario({"wait_ms": 40}, {"abort": True}))
    info = seen[-1][1]
    assert info.code == 1006
    assert info.clean is False
    assert seen[-1][0] == 40


def test_the_mulaw_speech_is_never_digital_silence() -> None:
    """A low level encodes to the mu-law zero byte unless the encoder avoids it: speech is
    active."""
    seen = listen("twilio", scenario({"speak": {"frames": 1, "level": 1}}, {"hang_up": 1000}))
    (frame,) = [e for _, e in seen if e is not None and getattr(e, "kind", "") == "audio"]
    assert any(
        importlib.import_module("tracer_evalcore.recorder").decode_to_pcm16("mulaw", frame.audio)
    )


def test_the_connection_refuses_a_send_after_the_close() -> None:
    clock = ManualClock()

    async def main() -> None:
        conn = await fa().connect_fake_agent(
            fa().parse_fake_agent(scenario({"wait_ms": 10_000})), dialect_of("protobuf"), clock
        )()
        await conn.send(b"x")
        await conn.close(1000)
        assert conn.close_info.code == 1000
        with pytest.raises(mod("errors").TransportError):
            await conn.send(b"y")

    run_virtual(clock, main)


def test_the_fake_agent_module_has_no_socket_and_no_wall_clock() -> None:
    import ast

    path = Path(fa().__file__)
    tree = ast.parse(path.read_text("utf-8"))
    imported = {
        (n.names[0].name if isinstance(n, ast.Import) else (n.module or "")).split(".")[0]
        for n in ast.walk(tree)
        if isinstance(n, ast.Import | ast.ImportFrom)
    }
    assert not imported & {"socket", "ssl", "time", "datetime", "websockets", "os", "secrets"}


# ------------------------------------------------------------------------------------ allowlist


def al() -> Any:
    return mod("allowlist")


@pytest.mark.parametrize(
    ("written", "normal"),
    [
        ("ws://127.0.0.1:8765/ws-client", "ws://127.0.0.1:8765/ws-client"),
        ("WS://LocalHost:80/Ws", "ws://localhost:80/Ws"),
        ("ws://localhost", "ws://localhost:80/"),
        ("wss://Agent.Example/ws", "wss://agent.example:443/ws"),
        ("https://agent.example/api/offer", "https://agent.example:443/api/offer"),
        ("ws://[::1]:9000/p", "ws://[::1]:9000/p"),
        ("+919876543210", "+919876543210"),
    ],
)
def test_normalisation_lowercases_scheme_and_host_and_writes_the_default_port(
    written: str, normal: str
) -> None:
    assert al().normalise_target(written) == normal


@pytest.mark.parametrize(
    "bad",
    [
        "ws://user:pw@localhost/ws",
        "ws://localhost/ws?token=1",
        "ws://localhost/ws#frag",
        "ws://example.com/ws",  # plain ws to a non-loopback host
        "ftp://localhost/ws",
        "ws://",
        "ws://localhost:0/ws",
        "ws://local host/ws",
        "ws://h\u00f6st.example/ws",
    ],
)
def test_a_target_with_user_information_query_fragment_or_a_bad_host_is_refused(bad: str) -> None:
    with pytest.raises(al().AllowlistError) as caught:
        al().normalise_target(bad)
    assert str(caught.value) == caught.value.code


def test_the_path_is_kept_byte_for_byte() -> None:
    assert al().normalise_target("ws://localhost:1/a%2Fb/Z") == "ws://localhost:1/a%2Fb/Z"
    assert al().normalise_target("ws://localhost:1/a") != al().normalise_target(
        "ws://localhost:1/a/"
    )


def test_entries_fields_comments_and_matching() -> None:
    text = (
        "# the operator's list\n\n"
        "ws://127.0.0.1:8765/ws-client   # a comment\n"
        "wss://agent.example/ws auth_token_env=AGENT_TOKEN origin=https://studio.example\n"
        "ws://localhost:9/ws bot_holds_telephony_credentials=false\n"
    )
    entries = al().parse_allowlist(text)
    assert [e.target for e in entries] == [
        "ws://127.0.0.1:8765/ws-client",
        "wss://agent.example:443/ws",
        "ws://localhost:9/ws",
    ]
    assert entries[1].auth_token_env == "AGENT_TOKEN"  # noqa: S105 - a NAME
    assert entries[1].origin == "https://studio.example"
    assert entries[2].bot_holds_telephony_credentials is False
    assert entries[0].auth_token_env is None
    assert entries[0].origin is None
    found = al().find_entry(entries, "WSS://AGENT.example:443/ws")
    assert found is entries[1]
    assert al().find_entry(entries, "wss://agent.example/ws/") is None  # exact, no prefix match
    assert al().find_entry(entries, "wss://agent.example/other") is None


@pytest.mark.parametrize(
    "line",
    [
        "ws://localhost/ws unknown=1",
        "ws://localhost/ws auth_token_env=a",
        "ws://localhost/ws auth_token_env=A auth_token_env=B",
        "ws://localhost/ws origin=https://Upper.example",
        "ws://localhost/ws bot_holds_telephony_credentials=maybe",
        "ws://localhost/ws origin=",
        "ws://localhost/ws novalue",
    ],
)
def test_an_unknown_repeated_or_malformed_field_refuses_the_file(line: str) -> None:
    with pytest.raises(al().AllowlistError) as caught:
        al().parse_allowlist(line + "\n")
    assert caught.value.code == "allowlist_field_invalid"


def test_a_repeated_entry_is_ambiguous() -> None:
    entries = al().parse_allowlist("ws://localhost/ws\nws://LOCALHOST:80/ws\n")
    with pytest.raises(al().AllowlistError) as caught:
        al().find_entry(entries, "ws://localhost/ws")
    assert caught.value.code == "allowlist_entry_repeated"


def test_the_allowlist_file_must_be_a_private_regular_file(tmp_path: Path) -> None:
    good = tmp_path / "allow.txt"
    good.write_text("ws://localhost/ws\n")
    good.chmod(0o600)
    assert al().read_allowlist(good)[0].target == "ws://localhost:80/ws"
    good.chmod(0o640)
    with pytest.raises(al().AllowlistError) as open_mode:
        al().read_allowlist(good)
    assert open_mode.value.code == "allowlist_mode_too_open"
    good.chmod(0o400)
    assert al().read_allowlist(good)
    link = tmp_path / "link.txt"
    os.symlink(good, link)
    with pytest.raises(al().AllowlistError) as linked:
        al().read_allowlist(link)
    assert linked.value.code == "allowlist_not_a_regular_file"
    with pytest.raises(al().AllowlistError) as missing:
        al().read_allowlist(tmp_path / "nope.txt")
    assert missing.value.code == "allowlist_unreadable"
    with pytest.raises(al().AllowlistError) as directory:
        al().read_allowlist(tmp_path)
    assert directory.value.code == "allowlist_not_a_regular_file"
    big = tmp_path / "big.txt"
    big.write_text("# " + "x" * al().MAX_ALLOWLIST_BYTES)
    big.chmod(0o600)
    with pytest.raises(al().AllowlistError) as large:
        al().read_allowlist(big)
    assert large.value.code == "allowlist_too_large"
    exact = tmp_path / "exact.txt"
    exact.write_text("# " + "x" * (al().MAX_ALLOWLIST_BYTES - 2))  # exactly the cap
    exact.chmod(0o600)
    assert al().read_allowlist(exact) == ()
    over = tmp_path / "over.txt"
    over.write_text("# " + "x" * (al().MAX_ALLOWLIST_BYTES - 1))  # one byte more
    over.chmod(0o600)
    with pytest.raises(al().AllowlistError) as just_over:
        al().read_allowlist(over)
    assert just_over.value.code == "allowlist_too_large"
    bad = tmp_path / "bad.txt"
    bad.write_bytes(b"\xff\xfe")
    bad.chmod(0o600)
    with pytest.raises(al().AllowlistError) as notutf:
        al().read_allowlist(bad)
    assert notutf.value.code == "allowlist_unreadable"
