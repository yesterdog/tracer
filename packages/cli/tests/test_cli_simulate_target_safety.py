# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P4-31 (target safety): what ``tracer simulate`` does with the target allowlist (ADR 0026 rule 16,
rule 14 step 2; the owner's update of 2026-10-27: WebSocket and WebRTC targets stay in Phase 4, the
telephone part is deferred, BL-311).

Contract: docs/phase4/api-surface-batch-g.md sections 6 and 7. The unit tests of the
allowlist module (``test_evalsim_fake_agent_and_allowlist``) pin the parser; these tests pin the
COMMAND: for every refusal exit 4, one ``error:`` line that is a code (never a path, a host, a
port, a value of the file), nothing on stdout, no key read (not the provider key and not the bearer
variable of an entry), no engine built, no connection made, no output directory created, and the
call trace stops at the allowlist step. The provider is a scripted ``httpx.MockTransport``, the
connector a recorder, the resolver a table: no socket and no real lookup.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from cli_batchf_support import KEY_CANARY  # type: ignore[import-not-found,unused-ignore]
from cli_p4e_support import (  # type: ignore[import-not-found,unused-ignore]
    TOKEN_ENV,
    TOKEN_VALUE,
    EnvRecorder,
    Files,
    SarvamSim,
    SimRun,
    VirtualClock,
    fake_connect,
    mod,
    profile_doc,
    real_args,
    run_sim,
    write_inputs,
)

ENTRY_URL = "ws://127.0.0.1:8765/ws-client"
PUBLIC = "93.184.216.34"


@dataclass
class Attempt:
    run: SimRun
    files: Files
    env: EnvRecorder
    sim: SarvamSim
    connects: list[Any]
    resolved: list[tuple[str, int]]

    def keys_read(self) -> list[str]:
        return [r for r in self.env.reads if r in ("SARVAM_API_KEY", TOKEN_ENV)]


def attempt(
    root: Path,
    *,
    url: str = ENTRY_URL,
    text: str | None = None,
    mode: int = 0o600,
    prepare: Callable[[Files], None] | None = None,
    answer: Sequence[str] | Exception = (PUBLIC,),
    drop_allowlist_option: bool = False,
) -> Attempt:
    """One real-engine run against ``url`` with the allowlist ``text`` (default: ``url`` itself)."""
    files = write_inputs(root, sim_profile=profile_doc(tts="sarvam", stt="sarvam"), url=url)
    if text is not None:
        files.allowlist.write_text(text, "utf-8")
    files.allowlist.chmod(mode)
    if prepare is not None:
        prepare(files)
    env = EnvRecorder({"SARVAM_API_KEY": KEY_CANARY, TOKEN_ENV: TOKEN_VALUE})
    sim = SarvamSim()
    connects: list[Any] = []
    resolved: list[tuple[str, int]] = []
    clock = VirtualClock()
    inner = fake_connect(files, clock)

    async def connect(request: Any) -> Any:
        connects.append(request)
        return await inner(request)

    def resolver(host: str, port: int) -> Sequence[str]:
        resolved.append((host, port))
        if isinstance(answer, Exception):
            raise answer
        return answer

    args = real_args(files, target=True)
    if drop_allowlist_option:
        at = args.index("--allowlist")
        del args[at : at + 2]
    run = run_sim(
        files,
        args=args,
        target=True,
        environ=env,
        transport=sim,
        clock=clock,
        world_kw={"connect": connect, "target_resolver": resolver},
    )
    return Attempt(run, files, env, sim, connects, resolved)


def assert_refused_at_the_allowlist(got: Attempt, code: str) -> None:
    """The shape every refusal of step 2 has (ADR 0026 rule 14 and invariant I4)."""
    run = got.run
    assert run.code == 4, (run.out, run.err)
    assert run.err == f"error: {code}\n", run.err
    assert run.out == ""
    assert got.keys_read() == [], got.env.reads
    assert got.connects == []
    assert got.sim.requests == []
    assert got.resolved == [] or code in ("target_address_refused", "target_unresolvable")
    assert not got.files.out.exists(), "a refusal left an output directory behind"
    assert run.trace == ["parse_inputs", "allowlist"], run.trace
    for secret in (str(got.files.allowlist), str(got.files.root), KEY_CANARY, TOKEN_VALUE):
        assert secret not in run.err


# ------------------------------------------------------------------------ not on the list


@pytest.mark.parametrize(
    "target",
    [
        "ws://127.0.0.1:8766/ws-client",  # another port
        "ws://127.0.0.1/ws-client",  # the default port (80) is not 8765
        "ws://127.0.0.1:8765/ws-client/",  # a trailing slash is another path
        "ws://127.0.0.1:8765/ws-client/more",  # a prefix match would let this through
        "ws://127.0.0.1:8765/ws",  # a shorter path
        "ws://127.0.0.1:8765/WS-CLIENT",  # the path is compared byte for byte
        "ws://127.0.0.1:8765/ws%2Dclient",  # and never percent-decoded
        "ws://127.0.0.1:8765/",  # the root
        "ws://[::1]:8765/ws-client",  # another loopback address
        "wss://127.0.0.1:8765/ws-client",  # another scheme
        "ws://localhost:8765/ws-client",  # a name is not the literal address of the entry
    ],
)
def test_target_not_on_allowlist_refused_before_any_key_or_socket(
    tmp_path: Path, target: str
) -> None:
    """ADR 0026 rule 16 and invariant I4: the comparison is exact after one normalisation (scheme
    and host in lower case, the default port written out, the path kept byte for byte); there is no
    prefix or wildcard match; a loopback target needs its entry like any other. A target that is
    not on the list is refused at step 2, exit 4, ``target_not_on_allowlist``, before any key is
    read and before any connection or lookup, and the run leaves nothing behind."""
    got = attempt(tmp_path, url=target, text=ENTRY_URL + "\n")
    assert_refused_at_the_allowlist(got, "target_not_on_allowlist")
    for piece in ("8766", "ws-client", "::1", "localhost"):
        assert piece not in got.run.err  # the target is never printed


def test_a_run_without_an_allowlist_is_refused_even_for_a_loopback_target(tmp_path: Path) -> None:
    got = attempt(tmp_path, drop_allowlist_option=True)
    assert_refused_at_the_allowlist(got, "allowlist_missing")


def test_a_telephone_number_entry_never_matches_a_url_target(tmp_path: Path) -> None:
    """The telephone part is deferred (BL-311): an E.164 entry parses but a WebSocket target is
    not on a list that holds only a number."""
    got = attempt(tmp_path, text="+919800000000\n")
    assert_refused_at_the_allowlist(got, "target_not_on_allowlist")
    assert "9800" not in got.run.err


def test_the_entry_may_be_written_in_another_case_and_with_the_default_port_left_out(
    tmp_path: Path,
) -> None:
    """Positive control of the normalisation: these runs are on the list and are connected once."""
    got = attempt(tmp_path / "a", url="ws://127.0.0.1:80/x", text="ws://127.0.0.1/x\n")
    assert got.run.code == 0, (got.run.out, got.run.err)
    assert len(got.connects) == 1
    got = attempt(tmp_path / "b", url=ENTRY_URL, text="WS://127.0.0.1:8765/ws-client\n")
    assert got.run.code == 0, (got.run.out, got.run.err)
    assert len(got.connects) == 1


def test_a_target_written_in_another_case_is_used_as_allowlisted(tmp_path: Path) -> None:
    """Defect found by the probe of this batch: a target ``WS://...`` matched its entry (after
    normalisation) but the raw text was then handed on, and the run ended in an unexpected failure
    (a ``ValidationError``) AFTER the call. What is connected and stored is the allowlisted form:
    the connector is given the normalised text, and the run is stored."""
    got = attempt(tmp_path, url="WS://127.0.0.1:8765/ws-client", text=ENTRY_URL + "\n")
    assert got.run.code == 0, (got.run.out, got.run.err)
    (request,) = got.connects
    assert request.url == ENTRY_URL
    stored = got.run.bundle()
    assert stored.run.mode == "simulated"


@pytest.mark.parametrize(
    ("target", "code"),
    [
        ("ws://127.0.0.1:8765/ws-client?token=x", "target_invalid"),
        ("ws://127.0.0.1:8765/ws-client#frag", "target_invalid"),
        ("ws://user:pw@127.0.0.1:8765/ws-client", "target_invalid"),
        ("ws://127.0.0.1:99999/ws-client", "target_invalid"),
        ("ws://exampl" + chr(0xE9) + ".test/ws", "target_invalid"),
        ("http://127.0.0.1:8765/ws-client", "target_invalid"),
        ("ws://example.test/ws", "insecure_target"),
    ],
)
def test_a_target_the_rule_forbids_is_refused_whatever_the_list_says(
    tmp_path: Path, target: str, code: str
) -> None:
    """A query (it often carries a token), a fragment, user information, a host outside ASCII, a
    bad port, a scheme the WebSocket dialects do not use and plain ``ws://`` to a host that is not
    loopback are refused at step 2 with their own code, before the list is searched."""
    safe = ENTRY_URL + "\n"
    got = attempt(tmp_path, url=target, text=safe)
    assert_refused_at_the_allowlist(got, code)


# --------------------------------------------------------------------- mode and links


def good_copy(files: Files) -> Path:
    path = files.root / "real-allowlist.txt"
    path.write_text(ENTRY_URL + "\n", "utf-8")
    path.chmod(0o600)
    return Path(path)


def as_symlink(files: Files) -> None:
    real = good_copy(files)
    files.allowlist.unlink()
    os.symlink(real, files.allowlist)


def as_dangling_symlink(files: Files) -> None:
    files.allowlist.unlink()
    os.symlink(files.root / "nowhere.txt", files.allowlist)


def as_directory(files: Files) -> None:
    files.allowlist.unlink()
    files.allowlist.mkdir(mode=0o700)


def as_fifo(files: Files) -> None:
    files.allowlist.unlink()
    os.mkfifo(files.allowlist, 0o600)


def as_missing(files: Files) -> None:
    files.allowlist.unlink()


def as_too_large(files: Files) -> None:
    files.allowlist.write_text(ENTRY_URL + "\n# " + "x" * 70_000 + "\n", "utf-8")
    files.allowlist.chmod(0o600)


def as_not_utf8(files: Files) -> None:
    files.allowlist.write_bytes(ENTRY_URL.encode() + b"\n\xff\xfe\n")
    files.allowlist.chmod(0o600)


@pytest.mark.parametrize("mode", [0o644, 0o640, 0o604, 0o660, 0o606, 0o666, 0o755, 0o777])
def test_allowlist_mode_and_link_rules(tmp_path: Path, mode: int) -> None:
    """ADR 0026 rule 16: the file must be a regular file with no group or other permission bit and
    must not be followed through a symbolic link. Every violation is refused at step 2 with its
    own code, before any key is read, and the command prints the code only: not the path of the
    file, not a line of it. The accepted modes are those with no group or other bit."""
    got = attempt(tmp_path / "mode", mode=mode)
    assert_refused_at_the_allowlist(got, "allowlist_mode_too_open")
    for ok in (0o600, 0o400, 0o700):
        accepted = attempt(tmp_path / f"ok{ok:o}", mode=ok)
        assert accepted.run.code == 0, (ok, accepted.run.out, accepted.run.err)
        assert len(accepted.connects) == 1
    for name, prepare, code in (
        ("symlink", as_symlink, "allowlist_not_a_regular_file"),
        ("dangling symlink", as_dangling_symlink, "allowlist_not_a_regular_file"),
        ("directory", as_directory, "allowlist_not_a_regular_file"),
        ("fifo", as_fifo, "allowlist_not_a_regular_file"),
        ("missing", as_missing, "allowlist_unreadable"),
        ("too large", as_too_large, "allowlist_too_large"),
        ("not utf-8", as_not_utf8, "allowlist_unreadable"),
    ):
        refused = attempt(tmp_path / name.replace(" ", "_"), prepare=prepare)
        assert_refused_at_the_allowlist(refused, code)


def test_a_group_readable_allowlist_is_refused_even_when_it_would_allow_the_target(
    tmp_path: Path,
) -> None:
    """The mode is about who else can read or change the list, not about its content: the same
    list at 0600 lets the same run through (the control above)."""
    got = attempt(tmp_path, mode=0o640)
    assert_refused_at_the_allowlist(got, "allowlist_mode_too_open")
    assert KEY_CANARY not in got.run.err


# ---------------------------------------------------------------------- entry fields

BAD_FIELDS = [
    "foo=bar",
    "origin=http://a.test origin=http://b.test",
    "auth_token_env=ONE auth_token_env=TWO",
    "bot_holds_telephony_credentials=false bot_holds_telephony_credentials=false",
    "auth_token_env=",
    "auth_token_env=lower_case",
    "auth_token_env=1LEADING",
    "auth_token_env=" + "A" * 65,
    "auth_token_env=HAS-DASH",
    "origin=http://a.test/path",
    "origin=http://a.test/",
    "origin=HTTP://A.TEST",
    "origin=ftp://a.test",
    "origin=",
    "origin=http://a.test:99999999",
    "bot_holds_telephony_credentials=yes",
    "bot_holds_telephony_credentials=True",
    "bot_holds_telephony_credentials=",
    "noequals",
    "Origin=http://a.test",
    "=x",
    "auth_token_env=A=B",
]


@pytest.mark.parametrize("fields", BAD_FIELDS)
def test_allowlist_entry_fields_unknown_or_repeated_refused(tmp_path: Path, fields: str) -> None:
    """ADR 0026 rule 16 (amended 2026-10-27, BL-299, BL-300): an unknown field, a repeated field
    (even with the same value) or a malformed value refuses the FILE, not the line: the run is
    refused at step 2 with ``allowlist_field_invalid`` and nothing of the value is printed."""
    got = attempt(tmp_path, text=f"{ENTRY_URL} {fields}\n")
    assert_refused_at_the_allowlist(got, "allowlist_field_invalid")


def test_a_malformed_line_for_another_target_refuses_the_whole_file(tmp_path: Path) -> None:
    text = (
        "# the agent under test\n"
        f"{ENTRY_URL}\n"
        "\n"
        "wss://other.example.test/ws unknown_field=CANARYFIELDVALUE\n"
    )
    got = attempt(tmp_path, text=text)
    assert_refused_at_the_allowlist(got, "allowlist_field_invalid")
    assert "CANARYFIELDVALUE" not in got.run.err
    assert "other.example" not in got.run.err


def test_a_forbidden_entry_for_another_target_also_refuses_the_whole_file(tmp_path: Path) -> None:
    for index, (line, code) in enumerate(
        (
            ("ws://user:pw@127.0.0.1:9/x", "target_invalid"),
            ("ws://127.0.0.1:9/x?token=1", "target_invalid"),
            ("ws://example.test/x", "insecure_target"),
        )
    ):
        got = attempt(tmp_path / str(index), text=f"{ENTRY_URL}\n{line}\n")
        assert_refused_at_the_allowlist(got, code)


def test_valid_fields_in_any_order_and_other_kinds_of_entries_are_accepted(tmp_path: Path) -> None:
    """Positive control: all three fields, in any order, a comment after an entry, a WebRTC
    signalling entry, a telephone entry and blank lines are a valid file; the run is connected
    once, with the labels of its own entry, and the bearer variable is read only now."""
    text = (
        "# local policy, not committed\n"
        "\n"
        f"{ENTRY_URL} origin=http://localhost:3000 auth_token_env={TOKEN_ENV}  # the sample bot\n"
        "https://agent.example.test/api/offer\n"
        "+919800000000\n"
        "wss://other.example.test/ws bot_holds_telephony_credentials=false\n"
    )
    got = attempt(tmp_path, text=text)
    assert got.run.code == 0, (got.run.out, got.run.err)
    (request,) = got.connects
    assert (request.labels.auth_token_env, request.labels.origin) == (
        TOKEN_ENV,
        "http://localhost:3000",
    )
    assert TOKEN_ENV in got.env.reads
    assert TOKEN_VALUE not in got.run.out + got.run.err


# --------------------------------------------------------------------- DNS (rule 16)


def test_a_public_name_that_resolves_to_a_private_address_is_refused_before_any_key(
    tmp_path: Path,
) -> None:
    """ADR 0026 rule 16 (DNS rebinding, BL-142): a host name on the list is resolved once and every
    address of the answer must be public; a public name that answers with a private, loopback,
    link-local or unspecified address is refused at step 2 (``target_address_refused``), exit 4,
    before any key. The name and the address are not printed."""
    url = "wss://agent.example.test/ws"
    for index, bad in enumerate(("10.0.0.5", "127.0.0.1", "169.254.169.254", "::1", "0.0.0.0")):  # noqa: S104
        got = attempt(tmp_path / str(index), url=url, text=url + "\n", answer=(PUBLIC, bad))
        assert_refused_at_the_allowlist(got, "target_address_refused")
        assert got.resolved == [("agent.example.test", 443)]
        assert "agent.example" not in got.run.err
        assert bad not in got.run.err


def test_a_name_that_does_not_resolve_is_refused_not_assumed_safe(tmp_path: Path) -> None:
    url = "wss://agent.example.test/ws"
    answers: list[Sequence[str] | Exception] = [[], OSError("no such host")]
    for index, answer in enumerate(answers):
        got = attempt(tmp_path / str(index), url=url, text=url + "\n", answer=answer)
        assert_refused_at_the_allowlist(got, "target_unresolvable")


def test_a_public_name_with_public_addresses_is_resolved_once_and_connected_as_allowlisted(
    tmp_path: Path,
) -> None:
    """The lookup happens once per run; the connector gets the allowlisted form of the target (the
    host in lower case, the default port written out)."""
    got = attempt(
        tmp_path,
        url="wss://Agent.Example.Test/ws",
        text="wss://agent.example.test:443/ws\n",
        answer=(PUBLIC, "2606:2800:220:1:248:1893:25c8:1946"),
    )
    assert got.run.code == 0, (got.run.out, got.run.err)
    assert got.resolved == [("agent.example.test", 443)]
    (request,) = got.connects
    assert request.url == "wss://agent.example.test:443/ws"


def test_localhost_is_resolved_and_must_be_loopback_only(tmp_path: Path) -> None:
    url = "ws://localhost:8765/ws-client"
    ok = attempt(tmp_path / "ok", url=url, text=url + "\n", answer=("127.0.0.1", "::1"))
    assert ok.run.code == 0, (ok.run.out, ok.run.err)
    assert ok.resolved == [("localhost", 8765)]
    bad = attempt(tmp_path / "bad", url=url, text=url + "\n", answer=("127.0.0.1", PUBLIC))
    assert_refused_at_the_allowlist(bad, "target_address_refused")


@pytest.mark.parametrize("literal", ["127.0.0.1", "10.1.2.3", "192.168.7.7", "[::1]"])
def test_a_literal_address_in_the_entry_is_the_operators_word_and_is_never_looked_up(
    tmp_path: Path, literal: str
) -> None:
    """A private or loopback address is reachable through an entry that names that literal: the
    agent on the operator's own network, the sample agents on this machine. No lookup is made.
    (Plain ``ws://`` is allowed for loopback only, so the private ones use ``wss://``.)"""
    scheme = "ws" if literal in ("127.0.0.1", "[::1]") else "wss"
    url = f"{scheme}://{literal}:8765/ws-client"
    got = attempt(tmp_path, url=url, text=url + "\n", answer=OSError("never asked"))
    assert got.run.code == 0, (got.run.out, got.run.err)
    assert got.resolved == []


def test_a_fake_agent_run_resolves_nothing(tmp_path: Path) -> None:
    """``--agent fake:SCENARIO`` has no target: no allowlist, no lookup."""
    files = write_inputs(tmp_path)
    resolved: list[Any] = []

    def resolver(host: str, port: int) -> Sequence[str]:
        resolved.append((host, port))
        return ()

    run = run_sim(files, world_kw={"target_resolver": resolver})
    assert run.code == 0, (run.out, run.err)
    assert resolved == []


def test_the_world_has_a_seam_for_the_resolver_and_a_system_default() -> None:
    import dataclasses

    names = {f.name for f in dataclasses.fields(mod("simulate").SimWorld)}
    assert "target_resolver" in names
    default = dataclasses.fields(mod("simulate").SimWorld)
    (field,) = [f for f in default if f.name == "target_resolver"]
    assert field.default is None
