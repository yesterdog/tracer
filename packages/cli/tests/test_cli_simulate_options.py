# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P4-23: the ``tracer simulate`` command line (SCOPE A.5; ADR 0026 rules 15, 19 and 24; ADR 0023
amendment A1; the Phase 3 batch E tests as models).

Contract: docs/phase4/api-surface-batch-e.md section 10.1. Pinned: the option table (every value
that decides a call or its spend is required and has no default), the spend caps of real engines,
the dry run (the plan and the spend estimate, nothing sent, nothing created), the exit codes with
their precedence 1, 5, 3, 2, 0, the deny-list on every output, and that neither a target address
nor a key is ever printed.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from cli_batchf_support import KEY_CANARY  # type: ignore[import-not-found,unused-ignore]
from cli_p4e_support import (  # type: ignore[import-not-found,unused-ignore]
    DENY,
    HEARD_0,
    HEARD_1,
    LINE_0,
    REF_TEXT,
    STAMP,
    TOKEN_ENV,
    TOKEN_VALUE,
    EnvRecorder,
    SarvamSim,
    VirtualClock,
    all_text,
    base_args,
    core,
    fake_connect,
    llm_caller,
    mod,
    profile_doc,
    real_args,
    real_env,
    real_files,
    run_sim,
    scenario_doc,
    sim_mod,
    tree,
    write_inputs,
)

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "uv.lock").is_file())
CLI_SRC = ROOT / "packages" / "cli" / "src" / "tracer_cli"


def drop(args: list[str], option: str) -> list[str]:
    out = list(args)
    at = out.index(option)
    del out[at : at + 2]
    return out


# ------------------------------------------------------------------------------------- caps


@pytest.mark.parametrize(
    ("engine", "needed"),
    [
        ("tts", "--max-tts-chars"),
        ("stt", "--max-stt-seconds"),
        ("llm", "--max-llm-tokens"),
    ],
)
def test_simulate_requires_caps_for_real_engines(tmp_path: Path, engine: str, needed: str) -> None:
    """ADR 0026 rule 15 and invariant I5: a real engine without its cap is refused (exit 4, step 4)
    before any key is read; the call cap is needed by any real engine; no cap has a default."""
    if engine == "llm":
        files = write_inputs(
            tmp_path, sim_profile=profile_doc(tts="sarvam", stt="sarvam", caller=llm_caller())
        )
        args = real_args(files)
        args[args.index("--caller") + 1] = "sarvam"
        args += ["--max-llm-tokens", "500"]
    else:
        files = real_files(tmp_path)
        args = real_args(files)
    args = drop(args, needed)
    env = EnvRecorder({"SARVAM_API_KEY": KEY_CANARY})
    sim = SarvamSim()
    run = run_sim(files, args=args, environ=env, transport=sim)
    assert run.code == 4
    assert run.out == ""
    assert run.err == f"error: cap_missing ({needed})\n"
    assert env.reads == []
    assert sim.requests == []
    assert not files.out.exists()
    # the call cap, alone, is needed as soon as any engine is real
    without_call = drop(real_args(files), "--max-call-seconds")
    if engine != "llm":
        run = run_sim(files, args=without_call, environ=env, transport=sim)
        assert run.err == "error: cap_missing (--max-call-seconds)\n"


def test_fake_engines_need_no_cap_and_a_given_cap_is_recorded(tmp_path: Path) -> None:
    files = write_inputs(tmp_path)
    run = run_sim(files)
    assert run.code == 0
    limits = run.bundle().snapshot.metadata["tracer.sim_profile"]["limits"]
    assert limits == {
        "max_call_seconds": None,
        "max_tts_chars": None,
        "max_llm_tokens": None,
        "max_stt_seconds": None,
        "min_call_interval_ms": 0,
    }
    again = write_inputs(tmp_path / "capped")
    run = run_sim(again, add=["--max-call-seconds", "90", "--max-tts-chars", "40"])
    assert run.code == 0
    limits = run.bundle().snapshot.metadata["tracer.sim_profile"]["limits"]
    assert (limits["max_call_seconds"], limits["max_tts_chars"]) == (90, 40)


@pytest.mark.parametrize("bad", ["0", "-1", "1.5", "abc", "", "14401", "0x10", " 5"])
def test_the_call_cap_is_a_whole_number_of_seconds_up_to_four_hours(
    tmp_path: Path, bad: str
) -> None:
    files = write_inputs(tmp_path)
    run = run_sim(files, add=["--max-call-seconds", bad])
    assert run.code == 64
    assert run.out == ""
    assert "--max-call-seconds" in run.err


def test_the_call_cap_bounds_the_call_by_the_smaller_of_the_two(tmp_path: Path) -> None:
    """ADR 0026 rule 9: the maximum duration is the smaller of the evaluator's and the cap's. The
    agent says nothing, the wait budget (3 s) is larger than both, so the call ends at the bound
    and ``connected_ms`` of the outcome shows which bound it was."""

    def ended_at(name: str, evaluator_ms: int, cap_s: int) -> tuple[str, int]:
        files = write_inputs(
            tmp_path / name,
            scenario=scenario_doc([{"wait_ms": 100_000}]),
            evaluator=json.loads(write_inputs(tmp_path / (name + "e")).evaluator.read_text())
            | {"max_duration_ms": evaluator_ms},
        )
        run = run_sim(files, add=["--max-call-seconds", str(cap_s)])
        outcome = run.bundle().run.metadata["tracer.sim_outcome"]
        return run.value("end reason"), outcome["connected_ms"]

    assert ended_at("cap", 60_000, 2) == ("max_duration", 2000)  # the cap's 2 s is the smaller
    assert ended_at("evaluator", 1000, 5) == ("max_duration", 1000)  # the evaluator's 1 s is
    assert ended_at("equal", 2000, 2) == ("max_duration", 2000)


# ----------------------------------------------------------------------------------- dry run

FAKE_DRY = [
    "dialect: pipecat-protobuf/1",
    "caller: scripted (2 lines)",
    "tts: fake, 14 characters to synthesise in 2 requests, 0 cache hits",
    "llm: none",
    "stt: fake, planned after the call from the agent audio",
    "max call seconds: not set",
    "max tts characters: not set",
    "max llm tokens: not set",
    "max stt seconds: not set",
    "dry run: nothing was sent",
]


def test_simulate_dry_run_prints_plan_and_sends_nothing(tmp_path: Path) -> None:
    """ADR 0026 rule 15: the plan and the spend estimate, no key read, nothing created, no socket,
    no engine built, no store opened, and neither the authorisation reference nor the allowlist is
    needed for a dry run."""
    files = write_inputs(tmp_path)
    run = run_sim(files, add=["--dry-run"], drop=["--authorisation-reference"])
    assert run.code == 0
    assert run.err == ""
    assert run.lines == FAKE_DRY
    assert not files.out.exists()
    assert run.trace == ["parse_inputs"]

    # the real engines: the caps are shown as given, the spend is estimated, no key is read
    real = real_files(tmp_path / "real")
    env = EnvRecorder({"SARVAM_API_KEY": KEY_CANARY})
    sim = SarvamSim()
    events: list[str] = []
    run = run_sim(
        real,
        args=real_args(real),
        add=["--dry-run"],
        environ=env,
        transport=sim,
        world_kw={
            "tts_factory": lambda s: events.append("t"),
            "stt_factory": lambda s: events.append("s"),
        },
    )
    assert run.code == 0
    assert run.err == ""
    assert run.lines == [
        "dialect: pipecat-protobuf/1",
        "caller: scripted (2 lines)",
        "tts: sarvam, 14 characters to synthesise in 2 requests, 0 cache hits",
        "llm: none",
        "stt: sarvam, planned after the call from the agent audio",
        "max call seconds: 60",
        "max tts characters: 100",
        "max llm tokens: not set",
        "max stt seconds: 60",
        "dry run: nothing was sent",
    ]
    assert env.reads == []
    assert sim.requests == []
    assert events == []
    assert not real.out.exists()


def test_the_dry_run_counts_the_cache_hits_of_an_earlier_run(tmp_path: Path) -> None:
    """ADR 0024 A9 and ADR 0026 rule 13: a re-run spends no TTS request for a cached line, and the
    dry run says so without creating anything."""
    files = real_files(tmp_path)
    first = run_sim(files, args=real_args(files), environ=real_env(), transport=SarvamSim())
    assert first.code == 0, (first.out, first.err)
    before = tree(files.out)
    dry = run_sim(
        files, args=real_args(files), add=["--dry-run"], environ=real_env(), transport=SarvamSim()
    )
    assert dry.lines[2] == "tts: sarvam, 14 characters to synthesise in 0 requests, 2 cache hits"
    assert tree(files.out) == before


def test_the_dry_run_of_an_llm_caller_shows_the_bounds_of_the_spend(tmp_path: Path) -> None:
    files = write_inputs(
        tmp_path,
        sim_profile=profile_doc(tts="sarvam", stt="sarvam", caller=llm_caller(max_turns=3)),
    )
    args = real_args(files)
    args[args.index("--caller") + 1] = "sarvam"
    run = run_sim(files, args=[*args, "--max-llm-tokens", "900", "--dry-run"])
    assert run.code == 0, run.err
    assert run.lines[1] == (
        "caller: llm (sarvam sarvam-105b, up to 3 turns, one regeneration at most each)"
    )
    assert run.lines[2] == "tts: lines are synthesised during the call, one request each"
    assert run.lines[3] == "llm: up to 6 requests of at most 50 tokens each"
    assert run.lines[4] == "stt: one request for each finished agent segment, during the call"
    assert "max llm tokens: 900" in run.lines


def test_the_dry_run_still_refuses_what_it_can_see(tmp_path: Path) -> None:
    files = write_inputs(tmp_path)
    files.evaluator.write_text("{broken")
    run = run_sim(files, add=["--dry-run"])
    assert run.code == 4
    assert run.out == ""
    assert "evaluator_not_json" in run.err


# ------------------------------------------------------------------------------------ exit codes


def test_exit_code_ranks_a_call_not_established_above_a_failed_transcription(
    tmp_path: Path,
) -> None:
    """The precedence is 5, then 3, then 2, then 0 (the 1 is the command's own): a bundle that is
    both not established and carries an ``stt_failed`` warning (only a hand-edited one can) is 5."""
    from tracer_evalschema.events import IngestWarning

    files = write_inputs(tmp_path)

    async def refuse(request: Any) -> Any:
        raise sim_mod("errors").SetupFailedError(code="connection_refused")

    run = run_sim(files, target=True, world_kw={"connect": refuse})
    assert run.code == 5, (run.out, run.err)
    bundle = run.bundle()
    warning = IngestWarning(code="stt_failed", path="/stt", message="SttServerError: chunk 0")
    flagged = bundle.model_copy(
        update={"run": bundle.run.model_copy(update={"ingest_warnings": [warning]})}
    )
    assert mod("simsummary").exit_code_of(bundle) == 5
    assert mod("simsummary").exit_code_of(flagged) == 5


def test_simulate_exit_codes(tmp_path: Path) -> None:
    """ADR 0026 rule 19 and invariant I9: 0 a call that was established with every result scored or
    not applicable; 1 an unexpected failure; 2 a result was not scored; 3 the speech-to-text of the
    agent failed; 4 an input refused; 5 the call was not established; 64 a usage error."""
    cli = mod("cli")
    assert cli.EXIT_CODES == {
        0: "scored", 1: "unexpected failure", 2: "not scored", 3: "speech-to-text failed",
        4: "input refused", 64: "usage",
    }  # the Phase 3 table is unchanged  # fmt: skip
    assert mod("simargs").SIM_EXIT_CODES.keys() == {0, 1, 2, 3, 4, 5, 64}

    ok = run_sim(write_inputs(tmp_path / "ok"))
    assert ok.code == 0

    silent = run_sim(write_inputs(tmp_path / "silent", scenario=scenario_doc([{"wait_ms": 9000}])))
    assert silent.code == 2  # timing values without an edge are errors, not zero

    refused = run_sim(write_inputs(tmp_path / "refused"), drop=["--authorisation-reference"])
    assert refused.code == 4

    usage = run_sim(write_inputs(tmp_path / "usage"), add=["--nope"])
    assert usage.code == 64

    async def refuse(request: Any) -> Any:
        raise sim_mod("errors").SetupFailedError(code="connection_refused")

    nocall = run_sim(write_inputs(tmp_path / "nocall"), target=True, world_kw={"connect": refuse})
    assert nocall.code == 5

    async def broken(request: Any) -> Any:
        raise RuntimeError("boom")

    crash = run_sim(write_inputs(tmp_path / "crash"), target=True, world_kw={"connect": broken})
    assert crash.code == 1
    assert crash.err == "error: unexpected failure (RuntimeError)\n"

    # the speech-to-text of the agent fails: no replay file for the one segment
    failed_files = write_inputs(tmp_path / "stt", scenario=scenario_doc())
    for chunk in (failed_files.root / "stt_replay").glob("chunk-*.json"):
        chunk.unlink()
    failed = run_sim(failed_files, target=True)
    assert failed.code == 3, (failed.out, failed.err)
    assert failed.value("agreement") == "not scored (missing input transcript, agent_transcript)"
    assert failed.value("ingest warnings") == "1"


def test_the_exit_code_precedence_is_1_5_3_2_0(tmp_path: Path) -> None:
    """When several apply the highest-precedence code wins. Here 3 beats 2: the agent speaks only
    while the first line is still being sent, so no gap exists (a timing error, exit 2 alone), and
    its transcription fails (exit 3)."""
    steps = [{"speak": {"frames": 10, "level": 1000, "text": HEARD_0}}]
    only_timing = run_sim(write_inputs(tmp_path / "a", scenario=scenario_doc(steps)))
    assert only_timing.code == 2
    assert only_timing.value("gap after the caller's line (median)").startswith("not measured")
    both = write_inputs(tmp_path / "b", scenario=scenario_doc(steps))
    for chunk in (both.root / "stt_replay").glob("chunk-*.json"):
        chunk.unlink()
    assert run_sim(both, target=True).code == 3
    function = mod("simsummary").exit_code_of
    assert callable(function)


# ----------------------------------------------------------------------- the agent's own export


def test_an_agent_export_is_compared_with_the_agent_channel_only(tmp_path: Path) -> None:
    files = write_inputs(tmp_path)
    run = run_sim(files, add=["--agent-transcript", str(files.export), "--declared-language", "mr"])
    assert run.code == 0, (run.out, run.err)
    value = run.value("agreement distance (MER)")
    assert value.startswith("0.0 (0 of 2; H 2, S 0, D 0, I 0)")
    assert run.value("flags") == "0"
    bundle = run.bundle()
    assert "agent_transcript" in bundle.run.available_inputs


def test_an_export_needs_its_declared_language_and_only_mr_is_accepted(tmp_path: Path) -> None:
    files = write_inputs(tmp_path)
    alone = run_sim(files, add=["--agent-transcript", str(files.export)])
    assert alone.code == 64
    assert "--agent-transcript and --declared-language go together" in alone.err
    wrong = run_sim(
        write_inputs(tmp_path / "w"),
        add=["--agent-transcript", str(files.export), "--declared-language", "hi"],
    )
    assert wrong.code == 4
    assert "language_not_supported" in wrong.err


# -------------------------------------------------------------------------------- the parser


def test_the_options_are_the_documented_ones_and_none_is_a_key(tmp_path: Path) -> None:
    cli = mod("cli")
    parser = cli.build_parser()
    sub = next(a for a in parser._actions if getattr(a, "choices", None))
    simulate = sub.choices["simulate"]
    options = {o for a in simulate._actions for o in a.option_strings}
    assert options >= {
        "--target", "--agent", "--allowlist", "--evaluator", "--persona", "--profile",
        "--sim-profile", "--tts", "--caller", "--stt", "--max-call-seconds", "--max-tts-chars",
        "--max-llm-tokens", "--max-stt-seconds", "--authorisation-reference", "--out",
        "--agent-transcript", "--declared-language", "--env-file", "--dry-run",
    }  # fmt: skip
    assert not any(
        re.search(r"api-key|--key|password|secret|--url|--number|--token", o) for o in options
    )
    required = {o for a in simulate._actions if a.required for o in a.option_strings}
    assert required == {
        "--evaluator",
        "--persona",
        "--sim-profile",
        "--tts",
        "--caller",
        "--stt",
        "--out",
    }
    for action in simulate._actions:
        if action.option_strings and action.dest not in ("help", "dry_run"):
            assert action.default is None, action.dest  # no default for anything


@pytest.mark.parametrize(
    ("add", "message"),
    [
        (["--target", "x"], "use exactly one of --target and --agent"),
        (["--cons", "x"], ""),
        (["--tts", "fake:dir"], "use sarvam or fake"),
        (["--tts", "openai"], "use sarvam or fake"),
        (["--caller", "fake:"], "use scripted, sarvam or fake:DIRECTORY"),
        (["--stt", "whisper"], "use sarvam, fake or fake:DIRECTORY"),
        (["--authorisation-reference", ""], "use 1 to 200 characters"),
        (["--authorisation-reference", " lead"], "no leading or trailing white space"),
        (["--authorisation-reference", "a\x00b"], "no control, format"),
        (["--authorisation-reference", "x" * 201], "use 1 to 200 characters"),
        (["--allowlist", "a.txt"], "--allowlist applies to --target only"),
    ],
)
def test_usage_errors_name_the_option(tmp_path: Path, add: list[str], message: str) -> None:
    files = write_inputs(tmp_path)
    args = base_args(files)
    for option in ("--tts", "--caller", "--stt", "--authorisation-reference"):
        if option in add:
            args = drop(args, option)
    run = run_sim(files, args=[*args, *add])
    assert run.code == 64, run.err
    assert run.out == "", run.err
    assert run.err.startswith("usage: ")
    assert message in run.err


def test_stt_fake_needs_a_fake_agent_and_a_replay_directory_does_not(tmp_path: Path) -> None:
    files = write_inputs(tmp_path)
    run = run_sim(files, target=True, add=["--stt", "fake"], drop=["--stt"])
    assert run.code == 64
    assert "--stt fake needs --agent fake:SCENARIO" in run.err
    ok = run_sim(files, target=True)
    assert ok.code == 0, (ok.out, ok.err)


def test_the_help_lists_the_exit_codes_and_claims_nothing() -> None:
    cli = mod("cli")
    import io

    out = io.StringIO()
    with pytest.raises(SystemExit) as caught:
        cli.run(
            ["simulate", "--help"],
            cli.Environment(
                stdout=out,
                stderr=io.StringIO(),
                environ={},
                clock=lambda: STAMP,
                sleep=lambda s: None,
            ),
        )
    assert caught.value.code == 0
    text = out.getvalue()
    assert text.startswith("usage: tracer simulate")
    assert text.isascii()
    assert re.search(r"5\s+call not established", text)
    assert not DENY.search(text), DENY.findall(text)
    top = io.StringIO()
    with pytest.raises(SystemExit):
        cli.run(
            ["--help"],
            cli.Environment(
                stdout=top,
                stderr=io.StringIO(),
                environ={},
                clock=lambda: STAMP,
                sleep=lambda s: None,
            ),
        )
    assert "simulate" in top.getvalue()
    assert not DENY.search(top.getvalue())


# ------------------------------------------------------------------------------ no claims


def test_simulate_output_never_claims_accuracy_or_quality(tmp_path: Path) -> None:
    """ADR 0023 amendment A1 and invariant IA1, ADR 0026 rule 24: the words of rule 1 and of A1
    appear in no stdout or stderr of the command, whatever happened (an ordinary call, a silent
    agent, a failed connection, a failed transcription, a dry run, every kind of refusal)."""
    outputs: list[str] = []
    outputs += [run_sim(write_inputs(tmp_path / "ok")).out]
    outputs += [
        run_sim(write_inputs(tmp_path / "s", scenario=scenario_doc([{"wait_ms": 9000}]))).out
    ]
    outputs += [run_sim(write_inputs(tmp_path / "d"), add=["--dry-run"]).out]
    barge = write_inputs(
        tmp_path / "b",
        sim_profile=profile_doc(barge_ins=[{"turn_index": 1, "barge_in_after_ms": 100}]),
        scenario=scenario_doc(
            [{"wait_ms": 300}, {"speak": {"frames": 30, "level": 1000, "text": HEARD_0}}]
        ),
    )
    barged = run_sim(barge)
    outputs += [barged.out]
    assert "time for the agent's audio to stop (median)" in barged.out
    stt_failed = write_inputs(tmp_path / "f")
    for chunk in (stt_failed.root / "stt_replay").glob("chunk-*.json"):
        chunk.unlink()
    outputs += [run_sim(stt_failed, target=True).out]
    refusals = [
        run_sim(write_inputs(tmp_path / "r1"), drop=["--authorisation-reference"]),
        run_sim(write_inputs(tmp_path / "r2"), add=["--bogus"]),
        run_sim(write_inputs(tmp_path / "r3", evaluator={"x": 1})),
    ]
    outputs += [r.out + r.err for r in refusals]
    for text in outputs:
        assert text, DENY.findall(text)
        assert not DENY.search(text), DENY.findall(text)
    assert all(text.isascii() for text in outputs)
    # the allowed words appear nowhere as labels either
    joined = "\n".join(outputs)
    assert "latency" not in joined.lower()
    assert "quality" not in joined.lower()


# ---------------------------------------------------------------- target, number and key


def failing_connect(kind: str, url: str) -> Any:
    async def connect(request: Any) -> Any:
        if kind == "crash":
            raise RuntimeError(url + TOKEN_VALUE + KEY_CANARY)
        raise sim_mod("errors").SetupFailedError(code="connection_refused")

    return connect


def recording_connect(seen: list[Any], files: Any, clock: VirtualClock) -> Any:
    async def connect(request: Any) -> Any:
        seen.append(request)
        return await fake_connect(files, clock)(request)

    return connect


def test_simulate_never_prints_target_number_or_key(tmp_path: Path) -> None:
    """ADR 0026 rule 16: the CLI never prints the target or a key. A recognisable address, a bearer
    token, the key and the authorisation reference appear in no stdout, no stderr, no report and no
    call log, whatever the outcome; the token and the key appear in no file at all."""
    url = "ws://127.0.0.1:8765/ws-TARGETCANARY"
    runs = []
    for kind in ("ok", "refused", "setup_failed", "crash"):
        files = write_inputs(
            tmp_path / kind, url=url, allowlist_line=f"{url} auth_token_env={TOKEN_ENV}"
        )
        env = {TOKEN_ENV: TOKEN_VALUE, "SARVAM_API_KEY": KEY_CANARY}
        args = base_args(files, target=True)
        seen: list[Any] = []
        clock = VirtualClock()
        world_kw: dict[str, Any] = {}
        if kind in ("setup_failed", "crash"):
            world_kw["connect"] = failing_connect(kind, url)
        if kind == "refused":
            args = drop(args, "--authorisation-reference")
        if kind == "ok":
            world_kw["connect"] = recording_connect(seen, files, clock)
        run = run_sim(files, args=args, environ=env, clock=clock, world_kw=world_kw)
        runs.append((kind, files, run))
        if kind == "ok":
            assert run.code == 0, (run.out, run.err)
            (request,) = seen
            assert request.url == url
            assert request.labels.auth_token_env == TOKEN_ENV
            assert request.token.authorization_header_value() == "Bearer " + TOKEN_VALUE
            assert TOKEN_VALUE not in repr(request)
            assert TOKEN_VALUE not in str(request)
    for kind, files, run in runs:
        streams = run.out + run.err
        assert "TARGETCANARY" not in streams, kind
        assert "8765" not in streams, kind
        for secret in (TOKEN_VALUE, KEY_CANARY, REF_TEXT):
            assert secret not in streams, (kind, secret)
        if files.out.exists():
            text = all_text(files.out)
            for secret in (TOKEN_VALUE, KEY_CANARY, REF_TEXT, TOKEN_ENV + "=" + TOKEN_VALUE):
                assert secret not in text, (kind, secret)
            for path in files.out.glob("report-*.html"):
                assert "TARGETCANARY" not in path.read_text("utf-8")
            for path in files.out.glob("sim_call_log.jsonl"):
                assert "TARGETCANARY" not in path.read_text("ascii")
    ok = next(r for k, _, r in runs if k == "ok")
    document = ok.bundle().snapshot.metadata["tracer.sim_profile"]
    assert document["connection"]["auth_token_env"] == TOKEN_ENV  # the NAME, never the value
    assert TOKEN_VALUE not in json.dumps(document)


def test_the_authorisation_reference_is_stored_as_its_sha256_only(tmp_path: Path) -> None:
    """ADR 0026 rule 21: the hash, lower-case hex, in the outcome; the text nowhere."""
    import hashlib

    files = write_inputs(tmp_path)
    run = run_sim(files)
    outcome = run.bundle().run.metadata["tracer.sim_outcome"]
    assert (
        outcome["authorisation_reference_sha256"] == hashlib.sha256(REF_TEXT.encode()).hexdigest()
    )
    assert REF_TEXT not in all_text(files.out)
    assert REF_TEXT not in run.out + run.err
    other = run_sim(
        write_inputs(tmp_path / "o"),
        args=[
            *drop(base_args(write_inputs(tmp_path / "o")), "--authorisation-reference"),
            "--authorisation-reference",
            "another reference",
        ],
    )
    assert (
        other.bundle().run.metadata["tracer.sim_outcome"]["authorisation_reference_sha256"]
        != outcome["authorisation_reference_sha256"]
    )


# ------------------------------------------------------------------------------- source rules


def imported(path: Path) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text("utf-8"))):
        if isinstance(node, ast.Import):
            out |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            out.add(node.module.split(".")[0])
    return out


def test_only_the_command_module_imports_the_simulator() -> None:
    """The first import of ``tracer_evalsim`` is in ``simulate.py`` (which the command line loads
    only when a simulation runs); ``simargs`` imports the standard library alone; no other module
    of the command line imports ``websockets`` or ``aiortc``."""
    for path in sorted(CLI_SRC.glob("*.py")):
        names = imported(path)
        assert not names & {"websockets", "aiortc", "av"}, path.name
        if path.name != "simulate.py":
            assert "tracer_evalsim" not in names, path.name
    assert "tracer_evalsim" in imported(CLI_SRC / "simulate.py")
    stdlib = sys.stdlib_module_names
    assert imported(CLI_SRC / "simargs.py") <= stdlib | {"__future__"}


def test_importing_the_command_line_loads_neither_the_simulator_nor_its_stack() -> None:
    code = (
        "import json, sys\n"
        "import tracer_cli.cli as c\n"
        "c.build_parser()\n"
        "print(json.dumps(sorted(n for n in ('tracer_evalsim', 'websockets', 'aiortc', 'av')"
        " if n in sys.modules)))\n"
    )
    probe = subprocess.run(  # noqa: S603
        [sys.executable, "-I", "-c", code],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert probe.returncode == 0, probe.stderr
    assert probe.stdout.strip() == "[]"


def test_no_module_of_the_command_reads_the_environment_or_the_clock_outside_system() -> None:
    """The Phase 3 rule extends to the new modules: the process environment and the wall clock
    are read in ``Environment.system`` only."""
    pattern = re.compile(r"os\.environ|os\.getenv|datetime\.now|time\.time\(|time\.monotonic")
    for name in ("simulate.py", "simreport.py", "simsummary.py", "fakes.py", "simargs.py"):
        text = (CLI_SRC / name).read_text("utf-8")
        assert not pattern.search(text), name
    assert core("simtypes") is not None
    assert LINE_0
    assert HEARD_1
