# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""BL-354 / audit A-F5 and BL-355 / A-F6 at the commands: no key variable is routed to a host that
does not own it, and the two owner files are read by one reader.

Contract: docs/phase4/api-surface-batch-g2.md sections 3 and 4. Step 2 of ``tracer simulate``
(ADR 0026 rule 14) reads the target allowlist and the judge allowlist. Before any key is read:

* 3.1 a target ``auth_token_env`` equal to ``SARVAM_API_KEY`` is refused
  (``allowlist_auth_env_reserved``);
* 3.2 a judge ``key_env`` equal to ``SARVAM_API_KEY`` on a host other than ``api.sarvam.ai`` is
  refused (``allowlist_key_env_reserved``), in ``tracer simulate`` and in ``tracer judge``;
* 3.3 a target ``auth_token_env`` equal to ANY ``key_env`` of the judge allowlist is refused
  (``auth_token_env_is_judge_key_env``), before the DNS lookup of the judge host;
* 4.4 the two allowlist readers give the same code for the same fault.

A refusal is exit 4, one ``error:`` line with the code, nothing on stdout, no connection, no
request,
no read of any of the three key variables, no directory created. No test opens a socket and no key
is real: the values are canaries built from parts.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any

import pytest

from cli_p4e_support import (  # type: ignore[import-not-found,unused-ignore]
    EnvRecorder,
    run_sim,
    write_inputs,
)
from cli_p4j2_support import (  # type: ignore[import-not-found,unused-ignore]
    CANARY,
    ENV_VAR,
    PUBLIC_IP,
    PUBLIC_URL,
    JudgeSim,
    config_doc,
    judge_argv,
    public_add,
    public_files,
    resolver,
    run_cmd,
    two_answers,
    write_judge_files,
)

URL = "ws://127.0.0.1:8765/ws-client"
SARVAM = "SARVAM_API_KEY"
BEARER = "AGENT_BEARER"
#: stand-ins for the Sarvam key and the agent token, built from parts
SARVAM_VALUE = "sarvamcanary" + "-".join(["q1", "w2", "e3"]) + "".join(["r4", "t5"])
BEARER_VALUE = "agentcanary" + "-".join(["z9", "y8", "x7"]) + "".join(["w6", "v5"])
WATCHED = {SARVAM, ENV_VAR, BEARER}
OTHER_URL = "https://other-judge.example.test/v1/chat/completions"


def environment() -> EnvRecorder:
    return EnvRecorder({SARVAM: SARVAM_VALUE, ENV_VAR: CANARY, BEARER: BEARER_VALUE})


def judged_setup(
    tmp_path: Path, *, target_line: str, judge_lines: list[str] | None = None
) -> tuple[Any, Any]:
    files = write_inputs(tmp_path, scenario=two_answers(), url=URL, allowlist_line=target_line)
    lines = (
        judge_lines
        if judge_lines is not None
        else [f"{PUBLIC_URL} profile=openai/1 key_env={ENV_VAR}"]
    )
    jf = write_judge_files(
        files,
        config=config_doc("openai/1", PUBLIC_URL),
        allowlist_line="\n".join(lines),
    )
    return files, jf


def refused(
    run: Any, code: str, env: EnvRecorder, dns: Any, sim: JudgeSim | None, files: Any
) -> None:
    assert run.code == 4, (run.out, run.err)
    assert run.out == ""
    assert run.err == f"error: {code}\n"
    assert run.trace == ["parse_inputs", "allowlist"], run.trace
    assert [name for name in env.reads if name in WATCHED] == [], env.reads
    assert dns.seen == [], "the DNS lookup comes after the file checks"
    if sim is not None:
        assert sim.requests == []
    assert not files.out.exists()
    for secret in (SARVAM_VALUE, CANARY, BEARER_VALUE):
        assert secret not in run.out + run.err


# ------------------------------------------------------------------------------------ 3.1


def test_a_target_token_variable_equal_to_the_sarvam_key_variable_is_refused_at_step_2(
    tmp_path: Path,
) -> None:
    """BL-354 / A-F5 (3.1): a mistaken entry would send the Sarvam key to the agent as a bearer
    token. The run is refused at step 2 with the stable code, with no key read, no connection."""
    files = write_inputs(tmp_path, url=URL, allowlist_line=f"{URL} auth_token_env={SARVAM}")
    env, dns = environment(), resolver(PUBLIC_IP)
    calls: list[str] = []

    async def connect(request: Any) -> Any:
        calls.append("connect")
        raise AssertionError("a connection was made")

    run = run_sim(
        files,
        target=True,
        environ=env,
        world_kw={"connect": connect, "judge_resolver": dns},
    )
    refused(run, "allowlist_auth_env_reserved", env, dns, None, files)
    assert calls == []


# ------------------------------------------------------------------------------------ 3.2


def test_a_judge_key_variable_equal_to_the_sarvam_key_variable_is_refused_at_step_2(
    tmp_path: Path,
) -> None:
    """BL-354 / A-F5 (3.2): the Sarvam key would go to the judge host in the ``Authorization``
    header. Refused when the judge allowlist is read: before the DNS lookup and before any key."""
    files, jf = judged_setup(
        tmp_path,
        target_line=URL,
        judge_lines=[f"{PUBLIC_URL} profile=openai/1 key_env={SARVAM}"],
    )
    env, dns, sim = environment(), resolver(PUBLIC_IP), JudgeSim()
    run = run_sim(
        files,
        target=True,
        add=public_add(jf),
        environ=env,
        transport=sim,
        world_kw={"judge_resolver": dns},
    )
    refused(run, "allowlist_key_env_reserved", env, dns, sim, files)


def test_the_sarvam_key_variable_for_a_judge_entry_of_the_sarvam_host_is_not_refused_at_step_2(
    tmp_path: Path,
) -> None:
    """BL-354 / A-F5 (3.2): the one allowed line is not refused by the rule (it may be refused by
    another, e.g. the unverified profile, which is a different code)."""
    files, jf = judged_setup(
        tmp_path,
        target_line=URL,
        judge_lines=[
            f"{PUBLIC_URL} profile=openai/1 key_env={ENV_VAR}",
            f"https://api.sarvam.ai/v2/chat/completions profile=sarvam_v2/1 key_env={SARVAM}",
        ],
    )
    env, dns = environment(), resolver(PUBLIC_IP)
    run = run_sim(
        files,
        target=True,
        add=public_add(jf),
        environ=env,
        transport=JudgeSim(),
        world_kw={"judge_resolver": dns},
    )
    assert "allowlist_key_env_reserved" not in run.err
    assert run.code != 4, run.err
    assert "allowlist" in run.trace, run.err
    assert "judge" in run.trace, run.err


def test_tracer_judge_refuses_the_sarvam_key_variable_for_another_host_before_any_key(
    tmp_path: Path,
) -> None:
    """BL-354 / A-F5 (3.2) in ``tracer judge``: the same file rule, the same code, exit 4."""
    files = write_inputs(tmp_path, scenario=two_answers(), url=URL)
    jf = public_files(files)
    first = run_sim(
        files,
        add=public_add(jf),
        environ={ENV_VAR: CANARY},
        transport=JudgeSim(),
        world_kw={"judge_resolver": resolver(PUBLIC_IP)},
    )
    assert first.code == 0, (first.out, first.err)
    jf.allowlist.write_text(f"{PUBLIC_URL} profile=openai/1 key_env={SARVAM}\n", "utf-8")
    jf.allowlist.chmod(0o600)
    argv = [
        *judge_argv(files.out, first.value("bundle"), jf, "openai/1"),
        "--judge-allowlist", str(jf.allowlist),
        "--max-judge-requests", "5", "--max-judge-tokens", "100000",
        "--judge-authorisation-reference", "Approved to send the synthetic call text, ticket 21",
        "--judge-first-request-gate",
    ]  # fmt: skip
    env, sim, dns = environment(), JudgeSim(), resolver(PUBLIC_IP)
    result = run_cmd(argv, environ=env, transport=sim, world_kw={"judge_resolver": dns})
    assert (result.code, result.err) == (4, "error: allowlist_key_env_reserved\n")
    assert [name for name in env.reads if name in WATCHED] == []
    assert sim.requests == []
    assert dns.seen == []


# ------------------------------------------------------------------------------------ 3.3


def test_a_target_token_variable_equal_to_the_judge_key_variable_is_refused_at_step_2(
    tmp_path: Path,
) -> None:
    """BL-354 / A-F5 (3.3): the judge key would go to the agent's host as a bearer token (or the
    agent's token to the judge host). Both files are read, the names are compared, and the run is
    refused before the DNS lookup and before any key."""
    files, jf = judged_setup(tmp_path, target_line=f"{URL} auth_token_env={ENV_VAR}")
    env, dns, sim = environment(), resolver(PUBLIC_IP), JudgeSim()
    run = run_sim(
        files,
        target=True,
        add=public_add(jf),
        environ=env,
        transport=sim,
        world_kw={"judge_resolver": dns},
    )
    refused(run, "auth_token_env_is_judge_key_env", env, dns, sim, files)


def test_the_comparison_covers_every_entry_of_the_judge_allowlist_not_only_the_matched_one(
    tmp_path: Path,
) -> None:
    """BL-354 / A-F5 (3.3): the matched entry uses another variable; a second entry of the file
    names the agent's token variable. The file as a whole is refused."""
    files, jf = judged_setup(
        tmp_path,
        target_line=f"{URL} auth_token_env={BEARER}",
        judge_lines=[
            f"{PUBLIC_URL} profile=openai/1 key_env={ENV_VAR}",
            f"{OTHER_URL} profile=openai/1 key_env={BEARER}",
        ],
    )
    env, dns, sim = environment(), resolver(PUBLIC_IP), JudgeSim()
    run = run_sim(
        files,
        target=True,
        add=public_add(jf),
        environ=env,
        transport=sim,
        world_kw={"judge_resolver": dns},
    )
    refused(run, "auth_token_env_is_judge_key_env", env, dns, sim, files)


def test_distinct_variable_names_are_not_refused(tmp_path: Path) -> None:
    """BL-354 / A-F5 (3.3), the control: the agent's token, the judge key and the Sarvam key are
    three names; the run completes, each key goes only where it belongs."""
    files, jf = judged_setup(tmp_path, target_line=f"{URL} auth_token_env={BEARER}")
    env, dns, sim = environment(), resolver(PUBLIC_IP), JudgeSim()
    run = run_sim(
        files,
        target=True,
        add=public_add(jf),
        environ=env,
        transport=sim,
        world_kw={"judge_resolver": dns},
    )
    # the target path replays recorded speech-to-text that gives no answer windows, so the judge
    # items are errors (exit 2); what is pinned here is that step 2 let the run through
    assert run.code != 4, (run.out, run.err)
    assert run.err == "", (run.out, run.err)
    assert "judge" in run.trace
    assert "report" in run.trace
    assert SARVAM not in env.reads
    assert SARVAM_VALUE not in run.out + run.err
    assert all(r.headers["authorization"] == f"Bearer {CANARY}" for r in sim.requests)


def test_a_run_without_a_judge_does_not_compare_with_a_file_it_does_not_read(
    tmp_path: Path,
) -> None:
    """BL-354 / A-F5 (3.3): with no judge the only reserved name is the Sarvam one; any other
    variable is accepted (the judge allowlist is not read)."""
    files = write_inputs(tmp_path, url=URL, allowlist_line=f"{URL} auth_token_env={ENV_VAR}")
    run = run_sim(files, target=True, environ=environment())
    assert run.code == 0, (run.out, run.err)
    assert run.err == "", (run.out, run.err)


# ------------------------------------------------------------------------------------ 4.4


def test_both_readers_give_the_same_code_for_the_same_fault(tmp_path: Path) -> None:
    """BL-355 / A-F6 (4.4): the target reader (tracer-evalsim) and the judge reader
    (tracer-evalproviders) are given the same files in the same states and name the same code."""
    from tracer_evalproviders.judgehosts import JudgeAllowlistError, read_judge_allowlist
    from tracer_evalsim.allowlist import AllowlistError, read_allowlist

    def code(reader: Any, error: type[Exception], path: Path) -> str:
        with pytest.raises(error) as caught:
            reader(path)
        return str(caught.value.code)  # type: ignore[attr-defined]

    def state(name: str) -> Path:
        path = tmp_path / name
        text = f"{PUBLIC_URL} profile=openai/1 key_env={ENV_VAR}\n"
        path.write_text(text, "utf-8")
        path.chmod(0o600)
        return path

    # the same bytes are a valid file for the judge reader and an invalid one for the target reader
    # (different formats), so the faults compared are those of the FILE, not of its text
    plain = tmp_path / "plain"
    plain.write_text("# nothing\n", "utf-8")
    plain.chmod(0o600)
    assert read_allowlist(plain) == ()
    assert read_judge_allowlist(plain) == ()

    cases: dict[str, Path] = {}
    wide = state("wide")
    wide.chmod(0o640)
    cases["wide"] = wide
    link = tmp_path / "link"
    os.symlink(plain, link)
    cases["link"] = link
    hard = tmp_path / "hard"
    os.link(plain, hard)
    cases["hard"] = hard
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo, 0o600)
    cases["fifo"] = fifo
    cases["directory"] = tmp_path
    cases["missing"] = tmp_path / "missing"
    big = tmp_path / "big"
    big.write_text("# " + "x" * 65_536 + "\n", "utf-8")
    big.chmod(0o600)
    cases["big"] = big
    odd = tmp_path / "odd"
    odd.write_bytes(b"\xff\xfe")
    odd.chmod(0o600)
    cases["not_utf8"] = odd
    expected = {
        "wide": "allowlist_mode_too_open",
        "link": "allowlist_not_a_regular_file",
        "hard": "allowlist_not_a_regular_file",
        "fifo": "allowlist_not_a_regular_file",
        "directory": "allowlist_not_a_regular_file",
        "missing": "allowlist_unreadable",
        "big": "allowlist_too_large",
        "not_utf8": "allowlist_unreadable",
    }
    for name, path in cases.items():
        target = code(read_allowlist, AllowlistError, path)
        judge = code(read_judge_allowlist, JudgeAllowlistError, path)
        assert target == judge == expected[name], (name, target, judge)
    assert stat.S_IMODE(os.stat(plain).st_mode) == 0o600
