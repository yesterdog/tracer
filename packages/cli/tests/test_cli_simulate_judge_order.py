# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
# ruff: noqa: E501
"""P4-J17: ``tracer simulate`` with a golden set and a judge (ADR 0030 rules 1, 3, 14, 15, 17, 18
and 20; SCOPE J.3 Q-J6; the batch E order tests as models).

Contract: docs/phase4/api-surface-batch-j2.md sections 2 to 6. Pinned here: the judge is step 13b
(after scoring, before the store), every refusal of the judge happens at its step and before any
key is read or any connection is made, the caps of a profile engine are required with no default,
the usage of every answered judge request survives a Ctrl-C, the dry run prints the judge plan and
sends nothing, and a judge error is exit 2. The provider is an ``httpx.MockTransport``.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from cli_p4e_support import (  # type: ignore[import-not-found,unused-ignore]
    EnvRecorder,
    base_args,
    llm_caller,
    mod,
    profile_doc,
    run_sim,
    write_inputs,
)
from cli_p4j2_support import (  # type: ignore[import-not-found,unused-ignore]
    CANARY,
    ENV_VAR,
    PUBLIC_IP,
    PUBLIC_URL,
    REF_TEXT,
    JudgeSim,
    chat_response,
    config_doc,
    golden_doc,
    judge_add,
    loopback_add,
    public_add,
    public_files,
    resolver,
    run_cmd,
    run_judged,
    two_answers,
    user_document,
    verdict_for,
    write_judge_files,
)

#: the 15 steps of the batch E contract and the judged run (the judge is 13b, after scoring)
STEPS = (
    "parse_inputs",
    "allowlist",
    "authorisation",
    "caps",
    "output_probe",
    "keys_and_call_log",
    "presynthesis",
    "open_store",
    "connect",
    "call_loop",
    "hang_up",
    "post_call_stt",
    "scoring",
    "store_write",
    "report",
)
STEPS_JUDGED = (*STEPS[:13], "judge", *STEPS[13:])


def _drop(args: list[str], option: str) -> list[str]:
    at = args.index(option)
    flag = option == "--judge-first-request-gate"
    return [*args[:at], *args[at + (1 if flag else 2) :]]


def test_simulate_with_golden_order_of_operations_pinned(tmp_path: Path) -> None:
    """ADR 0030 rule 20: the golden set and the configuration are parsed at step 1, the owner's
    judge allowlist and the DNS answers are checked at step 2, the key variable of the matched entry
    is first read at step 6, no request is sent before the judge (13b), the judge directory appears
    with the first request and the store comes after the judge. A run without a golden set has the
    steps of batch E and no 13b."""
    simulate = mod("simulate")
    assert simulate.STEPS == STEPS
    assert simulate.STEPS_WITH_JUDGE == STEPS_JUDGED
    files = write_inputs(tmp_path / "judged", scenario=two_answers())
    jf = public_files(files)
    env = EnvRecorder({ENV_VAR: CANARY})
    sim = JudgeSim()
    dns = resolver(PUBLIC_IP)
    seen: list[tuple[str, bool, int, int, bool, bool]] = []

    def trace(step: str) -> None:
        seen.append(
            (
                step,
                ENV_VAR in env.reads,
                len(sim.requests),
                len(dns.seen),
                files.out.exists(),
                (files.out / "judge").exists(),
            )
        )

    run = run_sim(
        files,
        add=public_add(jf),
        environ=env,
        transport=sim,
        world_kw={"trace": trace, "judge_resolver": dns},
    )
    assert run.code == 0, (run.out, run.err)
    assert tuple(row[0] for row in seen) == STEPS_JUDGED
    by_step = {row[0]: row[1:] for row in seen}
    # (key read, judge requests, dns lookups, out exists, judge dir exists) at the start of the step
    assert by_step["parse_inputs"] == (False, 0, 0, False, False)
    assert by_step["authorisation"] == (False, 0, 1, False, False), "the DNS answers: step 2"
    assert by_step["keys_and_call_log"] == (False, 0, 1, False, False), "no key before step 6"
    assert by_step["presynthesis"][0] is True, "the key is read at step 6"
    assert by_step["presynthesis"][3] is True
    assert by_step["scoring"][1:3] == (0, 1)
    assert by_step["scoring"][4] is False
    assert by_step["judge"] == (True, 0, 1, True, False), "13b starts with nothing sent"
    assert by_step["store_write"][1] == 2, "two items, one request each, before the store"
    assert by_step["store_write"][4] is True
    assert len(sim.requests) == 2
    assert {r.headers["authorization"] for r in sim.requests} == {f"Bearer {CANARY}"}
    # the judge authorisation reference: only its SHA-256 is said, after the bundle line
    digest = hashlib.sha256(REF_TEXT.encode("utf-8")).hexdigest()
    lines = run.out.splitlines()
    at = next(n for n, line in enumerate(lines) if line.startswith("bundle: "))
    assert lines[at + 1] == f"judge authorisation reference: sha256 {digest}"
    assert REF_TEXT not in run.out + run.err
    # a run without a golden set: the batch E steps, no 13b, no judge directory, no judge request
    plain = write_inputs(tmp_path / "plain")
    steps: list[str] = []
    other = run_sim(plain, world_kw={"trace": steps.append})
    assert other.code == 0
    assert tuple(steps) == STEPS
    assert not (plain.out / "judge").exists()


def _golden(doc: dict[str, Any]) -> Callable[[Any, Any, list[str]], list[str]]:
    def change(files: Any, jf: Any, args: list[str]) -> list[str]:
        jf.golden.write_text(json.dumps(doc, ensure_ascii=True), "utf-8")
        return args

    return change


def _golden_text(text: str) -> Callable[[Any, Any, list[str]], list[str]]:
    def change(files: Any, jf: Any, args: list[str]) -> list[str]:
        jf.golden.write_text(text, "utf-8")
        return args

    return change


def _config(**changes: Any) -> Callable[[Any, Any, list[str]], list[str]]:
    def change(files: Any, jf: Any, args: list[str]) -> list[str]:
        document = json.loads(jf.config.read_text("utf-8"))
        document.update(changes)
        for key in [k for k, v in changes.items() if v == "<delete>"]:
            del document[key]
        jf.config.write_text(json.dumps(document, ensure_ascii=True), "utf-8")
        return args

    return change


def _profile_config(profile: str, **changes: Any) -> Callable[[Any, Any, list[str]], list[str]]:
    def change(files: Any, jf: Any, args: list[str]) -> list[str]:
        jf.config.write_text(
            json.dumps(config_doc(profile, PUBLIC_URL, **changes), ensure_ascii=True), "utf-8"
        )
        jf.allowlist.write_text(f"{PUBLIC_URL} profile={profile} key_env={ENV_VAR}\n", "utf-8")
        args[args.index("--judge") + 1] = profile
        return args

    return change


def _no_option(option: str) -> Callable[[Any, Any, list[str]], list[str]]:
    return lambda files, jf, args: _drop(args, option)


def _allowlist(text: str, mode: int = 0o600) -> Callable[[Any, Any, list[str]], list[str]]:
    def change(files: Any, jf: Any, args: list[str]) -> list[str]:
        jf.allowlist.write_text(text, "utf-8")
        jf.allowlist.chmod(mode)
        return args

    return change


def _link_allowlist(files: Any, jf: Any, args: list[str]) -> list[str]:
    real = files.root / "real-allowlist.txt"
    real.write_text(jf.allowlist.read_text("utf-8"), "utf-8")
    real.chmod(0o600)
    jf.allowlist.unlink()
    os.symlink(real, jf.allowlist)
    return args


def _barge_in(files: Any, jf: Any, args: list[str]) -> list[str]:
    files.sim_profile.write_text(
        json.dumps(profile_doc(barge_ins=[{"turn_index": 1, "barge_in_after_ms": 100}])), "utf-8"
    )
    return args


def _llm_caller(files: Any, jf: Any, args: list[str]) -> list[str]:
    files.sim_profile.write_text(json.dumps(profile_doc(caller=llm_caller())), "utf-8")
    return [*args[: args.index("--caller") + 1], "sarvam", *args[args.index("--caller") + 2 :]]


def _replay_caller(files: Any, jf: Any, args: list[str]) -> list[str]:
    caller = {"mode": "replay", "max_line_chars": 200, "max_turns": 2, "end_sentinel": "END_CALL"}
    files.sim_profile.write_text(json.dumps(profile_doc(caller=caller)), "utf-8")
    at = args.index("--caller") + 1
    return [*args[:at], f"fake:{files.root / 'lines'}", *args[at + 1 :]]


def _fake_engine(scenario: str = "agrees") -> Callable[[Any, Any, list[str]], list[str]]:
    def change(files: Any, jf: Any, args: list[str]) -> list[str]:
        jf.config.write_text(json.dumps(config_doc("fake/1", None)), "utf-8")
        base = args[: args.index("--golden")]
        return [*base, *judge_add(jf, f"fake:{scenario}")]

    return change


def _engine_with_config(
    engine: str, profile: str, endpoint: str | None
) -> Callable[[Any, Any, list[str]], list[str]]:
    """The engine ``engine`` (no allowlist, no cap) with a configuration of ``profile``."""

    def change(files: Any, jf: Any, args: list[str]) -> list[str]:
        jf.config.write_text(json.dumps(config_doc(profile, endpoint)), "utf-8")
        base = args[: args.index("--golden")]
        return [*base, *judge_add(jf, engine.replace("DIR", str(files.root / "exchanges")))]

    return change


def _credential_shaped(files: Any, jf: Any, args: list[str]) -> list[str]:
    shaped = "sk-" + "".join(["Ab1Cd2Ef3Gh4", "Ij5Kl6Mn7Op8", "Qr9St0Uv1Wx2"])
    return _config(model=shaped)(files, jf, args)


#: (id, change, the refusal code, how many steps were traced, DNS lookups made)
REFUSALS: list[tuple[str, Callable[[Any, Any, list[str]], list[str]], str, int, int]] = [
    ("golden-not-json", _golden_text("{"), "golden_not_json", 1, 0),
    ("golden-unknown-key", _golden(golden_doc(extra=1)), "golden_unknown_key", 1, 0),
    (
        "golden-question-differs",
        _golden(
            golden_doc(
                [{**golden_doc()["items"][0], "question": "\u0928\u092e\u0938\u094d\u0924\u0947"}]
            )
        ),
        "golden_question_mismatch",
        1,
        0,
    ),
    (
        "golden-turn-missing",
        _golden(golden_doc([{**golden_doc()["items"][0], "scripted_turn_index": 9}])),
        "golden_turn_missing",
        1,
        0,
    ),
    ("golden-barge-in-turn", _barge_in, "golden_turn_barge_in", 1, 0),
    ("golden-with-llm-caller", _llm_caller, "golden_caller_not_scripted", 1, 0),
    ("golden-with-replay-caller", _replay_caller, "golden_caller_not_scripted", 1, 0),
    ("config-unknown-key", _config(api_key="x"), "judge_config_unknown_key", 1, 0),
    ("config-missing-max-tokens", _config(max_tokens="<delete>"), "judge_config_missing_key", 1, 0),
    ("config-key-shaped-value", _credential_shaped, "judge_config_key_shaped", 1, 0),
    ("config-bad-max-tokens", _config(max_tokens=0), "judge_config_max_tokens", 1, 0),
    (
        "config-profile-differs",
        _config(profile="openrouter/1"),
        "judge_config_profile_differs",
        1,
        0,
    ),
    ("config-unknown-profile", _config(profile="nonesuch/1"), "judge_config_profile", 1, 0),
    ("config-endpoint-missing", _config(endpoint=None), "judge_config_endpoint", 1, 0),
    ("fake-unknown-scenario", _fake_engine("nonesuch"), "unknown_scenario", 1, 0),
    (
        "fake-configuration-on-a-profile-engine",
        _config(profile="fake/1", endpoint=None),
        "judge_config_profile_differs",
        1,
        0,
    ),
    (
        "fake-configuration-on-a-replay-engine",
        _engine_with_config("replay:DIR", "fake/1", None),
        "judge_config_profile_differs",
        1,
        0,
    ),
    (
        "replay-engine-with-an-endpoint",
        _engine_with_config("replay:DIR", "openai/1", PUBLIC_URL),
        "judge_config_endpoint",
        1,
        0,
    ),
    (
        "fake-engine-with-a-real-configuration",
        _engine_with_config("fake:agrees", "openai/1", None),
        "judge_config_profile_differs",
        1,
        0,
    ),
    (
        "seed-on-anthropic-profile",
        _profile_config("anthropic_compat/1", seed=3),
        "seed_not_supported",
        1,
        0,
    ),
    (
        "sarvam-without-effort",
        _profile_config("sarvam_v2/1"),
        "reasoning_effort_required",
        1,
        0,
    ),
    ("openrouter-without-slug", _profile_config("openrouter/1"), "provider_slug_required", 1, 0),
    (
        "header-the-profile-does-not-list",
        _profile_config("openai/1", extra_headers={"HTTP-Referer": "https://example.test"}),
        "header_not_supported",
        1,
        0,
    ),
    ("no-judge-allowlist", _no_option("--judge-allowlist"), "judge_allowlist_missing", 2, 0),
    (
        "host-not-on-allowlist",
        _allowlist(
            "https://other.example.test/v1/chat/completions profile=openai/1 key_env=OTHER_KEY\n"
        ),
        "host_not_allowed",
        2,
        0,
    ),
    (
        "profile-differs-from-the-entry",
        _allowlist(f"{PUBLIC_URL} profile=openrouter/1 key_env={ENV_VAR}\n"),
        "profile_mismatch",
        2,
        0,
    ),
    (
        "allowlist-open-mode",
        _allowlist(f"{PUBLIC_URL} profile=openai/1 key_env={ENV_VAR}\n", 0o644),
        "allowlist_mode_too_open",
        2,
        0,
    ),
    ("allowlist-is-a-link", _link_allowlist, "allowlist_not_a_regular_file", 2, 0),
    (
        "plain-http-on-a-public-name",
        _allowlist(
            f"http://judge.example.test/v1/chat/completions profile=openai/1 key_env={ENV_VAR}\n"
        ),
        "allowlist_scheme_not_allowed",
        2,
        0,
    ),
    ("profile-not-verified", _no_option("--judge-first-request-gate"), "profile_unverified", 2, 0),
    (
        "no-authorisation-reference",
        _no_option("--judge-authorisation-reference"),
        "judge_authorisation_reference_missing",
        3,
        1,
    ),
    (
        "no-request-cap",
        _no_option("--max-judge-requests"),
        "cap_missing (--max-judge-requests)",
        4,
        1,
    ),
    ("no-token-cap", _no_option("--max-judge-tokens"), "cap_missing (--max-judge-tokens)", 4, 1),
]


@pytest.mark.parametrize(
    ("change", "code", "steps", "lookups"),
    [pytest.param(c, code, n, d, id=name) for name, c, code, n, d in REFUSALS],
)
def test_judge_refusals_before_any_key_read_or_connect(
    tmp_path: Path, change: Any, code: str, steps: int, lookups: int
) -> None:
    """ADR 0030 rules 3, 14, 15, 17 and 20: every refusal of the judge happens at its step, before
    the judge key variable is read, before an engine is built, before anything is sent and before a
    connection is made: exit 4, one line with a stable code (never a value), nothing created."""
    files = write_inputs(tmp_path, scenario=two_answers())
    jf = public_files(files)
    args = change(files, jf, [*base_args(files), *public_add(jf)])
    env = EnvRecorder({ENV_VAR: CANARY})
    sim = JudgeSim()
    dns = resolver(PUBLIC_IP)
    trace: list[str] = []
    run = run_sim(
        files,
        args=args,
        environ=env,
        transport=sim,
        world_kw={"trace": trace.append, "judge_resolver": dns},
    )
    assert run.code == 4, (run.out, run.err)
    assert run.err == f"error: {code}\n"
    assert run.out == ""
    assert tuple(trace) == STEPS_JUDGED[:steps]
    assert ENV_VAR not in env.reads, "the judge key is not read"
    assert sim.requests == []
    assert len(dns.seen) == lookups
    assert "connect" not in trace
    assert not files.out.exists(), "a refusal creates nothing"


def test_judge_key_variable_missing_is_refused_at_step_6_and_creates_nothing(
    tmp_path: Path,
) -> None:
    """The key is read by the variable name of the matched entry; when the variable is missing the
    run is refused at step 6 (a missing key leaves no directory), with a code and no value."""
    files = write_inputs(tmp_path, scenario=two_answers())
    jf = public_files(files)
    sim = JudgeSim()
    trace: list[str] = []
    run = run_sim(
        files,
        add=public_add(jf),
        environ={},
        transport=sim,
        world_kw={"trace": trace.append, "judge_resolver": resolver(PUBLIC_IP)},
    )
    assert run.code == 4
    assert run.err == "error: key_missing\n"
    assert tuple(trace) == STEPS_JUDGED[:6]
    assert sim.requests == []
    assert not files.out.exists()


def test_judge_caps_required_for_real_engine_no_defaults(tmp_path: Path) -> None:
    """ADR 0030 rule 17: a profile engine needs ``--max-judge-requests``, ``--max-judge-tokens`` and
    ``max_tokens`` in the configuration, with no default in code; the fake and replay engines send
    nothing, so a cap given with one is a usage error (exit 64)."""
    parser = mod("cli").build_parser()
    sub = parser._subparsers._group_actions[0].choices
    for command in ("simulate", "judge"):
        defaults = {a.dest: a.default for a in sub[command]._actions}
        assert defaults["max_judge_requests"] is None
        assert defaults["max_judge_tokens"] is None
        assert defaults["judge_authorisation_reference"] is None
        assert defaults["judge_first_request_gate"] is None
    # each cap is required for a profile engine, and with both the run goes through
    base = tmp_path / "base"
    files = write_inputs(base, scenario=two_answers())
    jf = write_judge_files(files)
    for option in ("--max-judge-requests", "--max-judge-tokens"):
        run = run_sim(files, add=_drop(loopback_add(jf), option), transport=JudgeSim())
        assert run.code == 4
        assert run.err == f"error: cap_missing ({option})\n"
    assert not files.out.exists()
    ok = run_sim(files, add=loopback_add(jf, requests=2, tokens=10_000), transport=JudgeSim())
    assert ok.code == 0, (ok.out, ok.err)
    # a cap number must be a positive whole number
    for bad in ("0", "-1", "1.5", "x", str(2**53)):
        refused = run_sim(
            files,
            add=[*_drop(loopback_add(jf), "--max-judge-requests"), "--max-judge-requests", bad],
            transport=JudgeSim(),
        )
        assert refused.code == 64, bad
    # the fake engine takes no cap
    fake_root = tmp_path / "fake"
    fake_files = write_inputs(fake_root, scenario=two_answers())
    fake_jf = write_judge_files(fake_files, config=config_doc("fake/1", None))
    for option, value in (
        ("--max-judge-requests", "5"),
        ("--max-judge-tokens", "500"),
        ("--judge-allowlist", str(fake_jf.allowlist)),
        ("--judge-authorisation-reference", "ref"),
        ("--judge-first-request-gate", ""),
    ):
        extra = [option] if option.endswith("gate") else [option, value]
        usage = run_sim(fake_files, add=[*judge_add(fake_jf, "fake:agrees"), *extra])
        assert usage.code == 64, option
        assert option in usage.err
    assert run_sim(fake_files, add=judge_add(fake_jf, "fake:agrees")).code == 0
    # the engine is a profile name, fake:SCENARIO or replay:DIRECTORY
    for bad_engine in (
        "nonsense",
        "OpenAI/1",
        "openai",
        "openai/",
        "fake:",
        "replay:",
        "openai/1234",
    ):
        args = judge_add(fake_jf, bad_engine)
        refused = run_sim(fake_files, add=args)
        assert refused.code == 64, bad_engine
        assert "profile name" in refused.err, bad_engine
    # the three options go together, and the judge options need them
    for subset in (["--golden"], ["--judge-config"], ["--judge"]):
        args = judge_add(fake_jf, "fake:agrees")
        at = args.index(subset[0])
        run = run_sim(fake_files, add=[*args[:at], *args[at + 2 :]])
        assert run.code == 64, subset
    solo = run_sim(fake_files, add=["--max-judge-requests", "5"])
    assert solo.code == 64


def test_judge_usage_survives_ctrl_c_mid_judging(tmp_path: Path) -> None:
    """ADR 0030 rule 18 and 20 (Phase 3 rule 16 (d)): a Ctrl-C while the third item is being
    judged keeps the usage of the two answered requests in ``usage-recovery-<UTC>.json`` (stage
    ``judge``, failed step ``judge``), prints its path, keeps the recording, stores no bundle, and
    the interrupt is raised again. The key and the call text are not in the file."""
    items = golden_doc()["items"]
    golden = golden_doc([*items, {**items[0], "id": "q2"}])  # the third item shares line 0
    files = write_inputs(tmp_path, scenario=two_answers())
    jf = public_files(files)
    jf.golden.write_text(json.dumps(golden, ensure_ascii=True), "utf-8")
    sim = JudgeSim([_answer, _answer, _interrupt])
    result = run_cmd(
        [
            "simulate",
            "--agent",
            f"fake:{files.scenario}",
            "--evaluator",
            str(files.evaluator),
            "--persona",
            str(files.persona),
            "--sim-profile",
            str(files.sim_profile),
            "--tts",
            "fake",
            "--caller",
            "scripted",
            "--stt",
            "fake",
            "--authorisation-reference",
            "Approved by the test operator, ticket 17",
            "--out",
            str(files.out),
            *public_add(jf),
        ],
        environ={ENV_VAR: CANARY},
        transport=sim,
        world_kw={"judge_resolver": resolver(PUBLIC_IP), "run_blocking": _inline},
    )
    assert isinstance(result.raised, KeyboardInterrupt)
    (recovery,) = list(files.out.glob("usage-recovery-*.json"))
    document = json.loads(recovery.read_text("ascii"))
    assert document["format"] == "tracer.usage_recovery/1"
    assert document["failed_step"] == "judge"
    assert document["error_class"] == "KeyboardInterrupt"
    assert [u["stage"] for u in document["usage_records"]] == ["judge", "judge"]
    assert [u["domain"] for u in document["usage_records"]] == ["evaluation"] * 2
    assert {u["model"] for u in document["usage_records"]} == {"judge-model-1"}
    text = recovery.read_text("ascii")
    assert CANARY not in text
    assert "OUTCOMECANARY" not in text
    assert result.out.splitlines()[-1] == f"usage recovery: {recovery}"
    assert len(sim.requests) == 3
    assert list(files.out.glob("recording-*.wav")), "the recording was kept"
    store = mod("store")
    engine = store.open_sqlite_store(files.out)
    try:
        import sqlalchemy as sa

        with engine.connect() as conn:
            tables = sa.inspect(conn).get_table_names()
            assert "run_bundle" in tables
            assert conn.execute(sa.text("select count(*) from run_bundle")).scalar() == 0
    finally:
        engine.dispose()
    assert not list(files.out.glob("report-*.html")), "no report before the judge ended"


async def _inline(function: Callable[[], Any]) -> Any:
    return function()


def _interrupt(request: Any, index: int) -> Any:
    raise KeyboardInterrupt


def _answer(request: Any, index: int) -> Any:
    return chat_response(verdict_for(user_document(request)), rid=f"c-{index}")


def test_simulate_dry_run_prints_judge_plan_and_sends_nothing(tmp_path: Path) -> None:
    """ADR 0030 rule 20: the dry run prints the judge plan after step 1: the engine, the model, the
    golden set, the number of requests it can make, the caps, and still says that nothing was sent.
    The allowlist is not read (a missing file is no error), the key variable is not read, DNS is not
    asked, the provider receives nothing and no directory is created."""
    files = write_inputs(tmp_path, scenario=two_answers())
    jf = public_files(files)
    args = public_add(jf)
    args[args.index("--judge-allowlist") + 1] = str(tmp_path / "no-such-allowlist.txt")
    env = EnvRecorder({ENV_VAR: CANARY})
    sim = JudgeSim()
    dns = resolver(PUBLIC_IP)
    run = run_sim(
        files,
        add=[*args, "--dry-run"],
        drop=["--authorisation-reference"],
        environ=env,
        transport=sim,
        world_kw={"judge_resolver": dns},
    )
    assert run.code == 0, (run.out, run.err)
    assert run.err == ""
    lines = run.out.splitlines()
    assert lines[-1] == "dry run: nothing was sent"
    assert [x for x in lines if x.startswith("judge")] == [
        "judge: uncalibrated signal, engine openai/1, model judge-model-1",
        "judge golden set: mr_p4j2_set, 2 items (1 all_facts, 1 equivalent_answer)",
        "judge requests: up to 4 (one for each item, one more for each regeneration)",
        "judge max tokens per request: 300",
        "judge max requests: 10",
        "judge max tokens: 100000",
    ]
    assert "keys" not in run.out
    assert ENV_VAR not in env.reads
    assert sim.requests == []
    assert dns.seen == []
    assert not files.out.exists()
    # the plan of a fake engine says the same without caps
    fake_files = write_inputs(tmp_path / "fake", scenario=two_answers())
    fake_jf = write_judge_files(fake_files, config=config_doc("fake/1", None))
    plan = run_sim(fake_files, add=[*judge_add(fake_jf, "fake:cannot_tell"), "--dry-run"])
    assert plan.code == 0
    assert "judge: uncalibrated signal, engine fake:cannot_tell, model judge-model-1" in plan.lines
    assert "judge max requests: not set" in plan.lines
    # a run without a golden set prints no judge line
    plain = run_sim(write_inputs(tmp_path / "plain"), add=["--dry-run"])
    assert [x for x in plain.lines if x.startswith("judge")] == []


@pytest.mark.parametrize(
    ("scenario", "code"),
    [
        ("agrees", 0),
        ("disagrees", 0),
        ("cannot_tell", 2),
        ("invalid", 2),
        ("invalid_once", 0),
        ("fenced", 2),
        ("auth_failure", 2),
    ],
)
def test_simulate_judge_errors_exit_2(tmp_path: Path, scenario: str, code: int) -> None:
    """ADR 0030 rule 20: a judge failure is exit 2 (``exit_code_of``: a result was not scored),
    a ``cannot_tell`` (an error result ``judge_undetermined``) too; a verdict, including
    ``disagrees``, is exit 0 (a disagreement is a result, not a failure); an invalid reply that the
    one regeneration repairs is exit 0. The bundle is stored and the report written in every case."""
    run, files, _ = run_judged(tmp_path, engine=f"fake:{scenario}")
    assert run.code == code, (scenario, run.out, run.err)
    assert run.value("store").endswith("tracer.sqlite")
    assert list(files.out.glob("report-*.html"))
    assert run.err == ""
    assert not (files.out / "judge").exists(), "a fake engine keeps no exchange file"
