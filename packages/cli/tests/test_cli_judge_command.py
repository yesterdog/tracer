# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P4-J18: ``tracer judge``, the rescoring of a stored judged run (ADR 0030 rule 21; FJ6 and FJ7;
ADR 0022 rule 5).

Contract: docs/phase4/api-surface-batch-j2.md section 7. One rescoring bundle for each repeat: it
shares every record of the call and carries its own judge results and judge usage. It is refused
when the run holds no judge definitions or when the engine's model is not the model of the
definitions. The provider is an ``httpx.MockTransport``; no test opens a socket.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Any

import pytest

from cli_p4e_support import EnvRecorder, run_sim, write_inputs  # type: ignore[import-not-found,unused-ignore]
from cli_p4j2_support import (  # type: ignore[import-not-found,unused-ignore]
    CANARY,
    ENV_VAR,
    PUBLIC_IP,
    REF_TEXT,
    JudgeSim,
    all_text,
    answer_with,
    bundle_count,
    config_doc,
    interrupt,
    judge_add,
    judge_argv,
    judge_doc,
    judge_results,
    load_bundle,
    loopback_add,
    loopback_judge_extra,
    public_add,
    public_files,
    resolver,
    run_cmd,
    run_judged,
    two_answers,
    write_judge_files,
)


def _judged(tmp_path: Path, engine: str = "fake:agrees", **kw: Any) -> tuple[Any, Any, Any]:
    run, files, jf = run_judged(tmp_path, engine=engine, **kw)
    assert run.code == 0, (run.out, run.err)
    return run, files, jf


def test_tracer_judge_rescoring_bundle_shares_records_and_adds_judge_results(
    tmp_path: Path,
) -> None:
    """ADR 0030 rule 21 and ADR 0022 rule 5: re-judging a stored judged run stores a NEW bundle of
    the same run: the run, the snapshot, the transcripts, the turns, the recording and every
    result that is not a judge result are the stored records (same ids, same bytes); the judge
    results are new records with the new verdicts. The first bundle is not changed. No verdict of
    the run is made."""
    run, files, jf = _judged(tmp_path)
    original = load_bundle(files.out, run.value("bundle"))
    before = original.model_dump_json()
    result = run_cmd(judge_argv(files.out, run.value("bundle"), jf, "fake:disagrees"))
    assert result.code == 0, (result.out, result.err)
    assert result.err == ""
    assert result.value("rescoring of") == str(original.id)
    assert result.value("repeats") == "1"
    new_id = result.value("repeat 1 of 1")
    assert new_id.startswith("rbnd_")
    assert new_id != str(original.id)
    assert bundle_count(files.out) == 2
    new = load_bundle(files.out, new_id)
    assert new.id != original.id
    assert new.run == original.run
    assert new.snapshot == original.snapshot
    assert new.transcripts == original.transcripts
    assert new.turns == original.turns
    assert new.artifacts == original.artifacts
    assert new.verdict is None
    judge_ids = {r.id for r in judge_results(original).values()}
    shared = [r for r in original.results if r.id not in judge_ids]
    assert shared
    assert all(r in new.results for r in shared)
    assert not judge_ids & {r.id for r in new.results}, "the judge results are new records"
    assert len(new.results) == len(original.results)
    old, fresh = judge_results(original), judge_results(new)
    assert list(old) == list(fresh) == ["q0", "q1"]
    assert [r.value for r in old.values()] == ["agrees", "agrees"]
    assert [r.value for r in fresh.values()] == ["disagrees", "disagrees"]
    assert judge_doc(fresh["q0"])["reason_code"] == "fact_absent"
    assert judge_doc(fresh["q1"])["reason_code"] == "meaning_differs"
    for item in fresh.values():
        assert item.judge is not None
        assert item.judge.confidence is None
        assert item.passed is None
    assert (
        original.model_dump_json()
        == before
        == load_bundle(files.out, str(original.id)).model_dump_json()
    )
    # what the command says
    assert result.value("judge disagrees") == "2 of 2 items"
    assert result.value("judge changed from the stored run") == "2 of 2 items"
    assert result.value("judge") == (
        "uncalibrated signal (golden set mr_p4j2_set, engine fake/1, model judge-model-1)"
    )
    assert result.value("store").endswith("tracer.sqlite")


def test_tracer_judge_refuses_run_without_judge_definitions(tmp_path: Path) -> None:
    """ADR 0030 rule 21: the snapshot fixes the definitions, so a run that was simulated without a
    golden set cannot be judged afterwards (FJ6): exit 4 with a code, nothing is stored, no key is
    read, no request is sent."""
    files = write_inputs(tmp_path, scenario=two_answers())
    plain = run_sim(files)
    assert plain.code == 0
    jf = write_judge_files(files)
    sim, env = JudgeSim(), EnvRecorder({ENV_VAR: CANARY})
    result = run_cmd(
        judge_argv(
            files.out, plain.value("bundle"), jf, "local_loopback/1", *loopback_judge_extra(jf)
        ),
        environ=env,
        transport=sim,
    )
    assert result.code == 4
    assert result.err == "error: run_has_no_judge_definitions\n"
    assert result.out == ""
    assert bundle_count(files.out) == 1
    assert sim.requests == []
    assert ENV_VAR not in env.reads


def test_tracer_judge_model_must_equal_definition_model_ref(tmp_path: Path) -> None:
    """ADR 0030 rule 21: the engine's model (and profile) must be the ``judge_model_ref`` of the
    definitions in the snapshot; another model needs a new run (FJ6). Refused, nothing sent,
    nothing stored."""
    run, files, _jf = _judged(tmp_path)
    other_model = write_judge_files(files, config=config_doc("fake/1", None, model="other-model-2"))
    sim = JudgeSim()
    for config_files, engine in ((other_model, "fake:agrees"),):
        result = run_cmd(judge_argv(files.out, run.value("bundle"), config_files, engine))
        assert result.code == 4
        assert result.err == "error: judge_model_differs\n"
    other_profile = write_judge_files(
        files,
        config=config_doc("local_loopback/1", None),
    )
    result = run_cmd(
        judge_argv(files.out, run.value("bundle"), other_profile, "replay:" + str(tmp_path)),
        transport=sim,
    )
    assert result.code == 4
    assert result.err == "error: judge_model_differs\n"
    assert sim.requests == []
    assert bundle_count(files.out) == 1


def test_tracer_judge_repeats_store_one_bundle_each(tmp_path: Path) -> None:
    """ADR 0030 rule 21 (FJ7): K repeats re-judge the same stored answers K times; each is one
    rescoring bundle (distinct ids, distinct judge results, judge usage of its own, an exchange
    directory of its own). The count of items whose result changed against the stored run measures
    the judge, not the agent. The caps and the exchange files belong to one repeat."""
    sim = JudgeSim()
    run, files, jf = _judged(tmp_path, "local_loopback/1", sim=sim)
    assert len(sim.requests) == 2
    original = load_bundle(files.out, run.value("bundle"))
    script = [
        answer_with("agrees"), answer_with("agrees"),  # repeat 1: as stored
        answer_with("disagrees"), answer_with("agrees"),  # repeat 2: item q0 flips
        answer_with("agrees"), answer_with("agrees"),  # repeat 3: as stored
    ]  # fmt: skip
    again = JudgeSim(script)
    result = run_cmd(
        judge_argv(
            files.out,
            run.value("bundle"),
            jf,
            "local_loopback/1",
            "--repeats",
            "3",
            *loopback_judge_extra(jf, requests=2),
        ),
        transport=again,
    )
    assert result.code == 0, (result.out, result.err)
    assert len(again.requests) == 6
    assert result.value("repeats") == "3"
    ids = [result.value(f"repeat {k} of 3") for k in (1, 2, 3)]
    assert len(set(ids)) == 3
    assert str(original.id) not in ids
    assert [x for x in result.lines if x.startswith("judge changed")] == [
        "judge changed from the stored run: 0 of 2 items",
        "judge changed from the stored run: 1 of 2 items",
        "judge changed from the stored run: 0 of 2 items",
    ]
    assert [x for x in result.lines if x.startswith("judge usage")] == [
        "judge usage: 2 requests, 240 input tokens, 30 output tokens"
    ] * 3
    assert bundle_count(files.out) == 4
    bundles = [load_bundle(files.out, i) for i in ids]
    seen: set[Any] = set()
    for bundle in bundles:
        assert bundle.run == original.run
        assert bundle.transcripts == original.transcripts
        mine = judge_results(bundle)
        assert list(mine) == ["q0", "q1"]
        assert not seen & {r.id for r in mine.values()}
        seen |= {r.id for r in mine.values()}
        assert [u.stage for u in bundle.usage_records if u.stage == "judge"] == ["judge", "judge"]
    assert [judge_results(b)["q0"].value for b in bundles] == ["agrees", "disagrees", "agrees"]
    # one exchange directory for each repeat, named by its bundle, private; the original's is kept
    judge_dir = files.out / "judge"
    names = sorted(p.name for p in judge_dir.iterdir())
    assert names == sorted([str(original.run.id), *ids])
    for name in ids:
        directory = judge_dir / name
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700
        assert sorted(p.name for p in directory.iterdir()) == ["item-q0-1.json", "item-q1-1.json"]
        assert {stat.S_IMODE(p.stat().st_mode) for p in directory.iterdir()} == {0o600}
    # the two requests of a repeat are inside its cap of 2, and a third would not be
    capped = JudgeSim()
    refused = run_cmd(
        judge_argv(
            files.out,
            run.value("bundle"),
            jf,
            "local_loopback/1",
            *loopback_judge_extra(jf, requests=1),
        ),
        transport=capped,
    )
    assert refused.code == 2
    assert len(capped.requests) == 1
    assert "judge_requests_cap_reached" in refused.out


def test_tracer_judge_replay_reproduces_the_stored_judgements(tmp_path: Path) -> None:
    """ADR 0030 rule 19 and invariant I6 at the command: ``replay:<dir>`` of the exchange files
    of the first run gives a rescoring whose judge results equal the stored ones (value, document,
    prompt hash, exchange hash, parameters), with no request and no usage."""
    sim = JudgeSim(default="disagrees")
    run, files, _jf = _judged(tmp_path, "local_loopback/1", sim=sim)
    original = load_bundle(files.out, run.value("bundle"))
    replay_config = write_judge_files(files, config=config_doc("local_loopback/1", None))
    quiet = JudgeSim()
    result = run_cmd(
        judge_argv(
            files.out,
            run.value("bundle"),
            replay_config,
            f"replay:{files.out / 'judge' / str(original.run.id)}",
        ),
        transport=quiet,
    )
    assert result.code == 0, (result.out, result.err)
    assert quiet.requests == []
    assert result.value("judge changed from the stored run") == "0 of 2 items"
    assert result.value("judge usage") == "none"
    new = load_bundle(files.out, result.value("repeat 1 of 1"))
    old, fresh = judge_results(original), judge_results(new)
    for item_id in old:
        assert fresh[item_id].value == old[item_id].value == "disagrees"
        assert judge_doc(fresh[item_id]) == judge_doc(old[item_id])
        assert fresh[item_id].judge == old[item_id].judge
    assert [u for u in new.usage_records if u.stage == "judge"] == []


def test_tracer_judge_checks_the_golden_set_and_the_bundle(tmp_path: Path) -> None:
    """The golden set must be the one of the run (its SHA-256 is in the stored profile document),
    the bundle must exist, and the store must exist: refused with a code."""
    run, files, jf = _judged(tmp_path)
    bundle = run.value("bundle")
    edited = json.loads(jf.golden.read_text("utf-8"))
    edited["key"] = "mr_other_set"
    jf.golden.write_text(json.dumps(edited), "utf-8")
    result = run_cmd(judge_argv(files.out, bundle, jf, "fake:agrees"))
    assert (result.code, result.err) == (4, "error: golden_set_differs\n")
    jf.golden.write_text(json.dumps({**edited, "key": "mr_p4j2_set"}), "utf-8")
    cases = [
        (str(files.out), "rbnd_01a17f10-4888-7637-86a0-1af7ad452c4e", "bundle_not_found"),
        (str(files.out), "not-an-id", "bundle_id_invalid"),
        (str(tmp_path / "no-such-store"), bundle, "store_not_found"),
    ]
    for out, bundle_id, code in cases:
        refused = run_cmd(
            [
                "judge",
                "--out",
                out,
                "--bundle",
                bundle_id,
                "--golden",
                str(jf.golden),
                "--judge-config",
                str(jf.config),
                "--judge",
                "fake:agrees",
            ]
        )
        assert (refused.code, refused.err) == (4, f"error: {code}\n"), code
    assert not (tmp_path / "no-such-store").exists()
    assert bundle_count(files.out) == 1


@pytest.mark.parametrize(
    "extra",
    [
        ["--repeats", "0"],
        ["--repeats", "101"],
        ["--repeats", "x"],
        ["--max-judge-requests", "3"],  # a fake engine takes no cap
    ],
)
def test_tracer_judge_usage_errors(tmp_path: Path, extra: list[str]) -> None:
    run, files, jf = _judged(tmp_path)
    result = run_cmd(judge_argv(files.out, run.value("bundle"), jf, "fake:agrees", *extra))
    assert result.code == 64
    assert bundle_count(files.out) == 1
    for missing in ("--golden", "--judge-config", "--judge", "--bundle", "--out"):
        argv = judge_argv(files.out, run.value("bundle"), jf, "fake:agrees")
        at = argv.index(missing)
        assert run_cmd([*argv[:at], *argv[at + 2 :]]).code == 64, missing


def test_tracer_judge_dry_run_prints_the_plan_and_sends_nothing(tmp_path: Path) -> None:
    """The dry run of the command reads the store and the two files, prints the plan, reads no
    allowlist and no key, sends nothing and stores nothing."""
    sim = JudgeSim()
    run, files, jf = _judged(tmp_path, "local_loopback/1", sim=sim)
    env, quiet = EnvRecorder({ENV_VAR: CANARY}), JudgeSim()
    argv = judge_argv(files.out, run.value("bundle"), jf, "local_loopback/1", "--repeats", "2")
    argv += ["--judge-allowlist", str(tmp_path / "no-such-allowlist.txt"), "--dry-run"]
    result = run_cmd(argv, environ=env, transport=quiet)
    assert result.code == 0, (result.out, result.err)
    lines = result.lines
    assert lines[-1] == "dry run: nothing was sent"
    assert "judge repeats: 2" in lines
    assert "judge: uncalibrated signal, engine local_loopback/1, model judge-model-1" in lines
    assert quiet.requests == []
    assert env.reads == []
    assert bundle_count(files.out) == 1


def test_tracer_judge_keeps_the_usage_of_a_repeat_that_is_interrupted(tmp_path: Path) -> None:
    """A Ctrl-C in the second repeat: the first repeat is stored, and the usage of every answered
    request of the command (two of the first repeat, one of the second) is in the recovery file."""
    run, files, jf = _judged(tmp_path, "local_loopback/1", sim=JudgeSim())
    sim = JudgeSim([answer_with("agrees"), answer_with("agrees"), answer_with("agrees"), interrupt])
    result = run_cmd(
        judge_argv(
            files.out,
            run.value("bundle"),
            jf,
            "local_loopback/1",
            "--repeats",
            "2",
            *loopback_judge_extra(jf),
        ),
        transport=sim,
    )
    assert isinstance(result.raised, KeyboardInterrupt)
    assert bundle_count(files.out) == 2
    (recovery,) = list(files.out.glob("usage-recovery-*.json"))
    document = json.loads(recovery.read_text("ascii"))
    assert document["failed_step"] == "judge"
    assert [u["stage"] for u in document["usage_records"]] == ["judge"] * 3
    assert result.out.splitlines()[-1] == f"usage recovery: {recovery}"
    assert os.stat(recovery).st_mode & 0o077 == 0


def test_tracer_judge_with_a_real_profile_needs_the_same_gates_as_the_run(tmp_path: Path) -> None:
    """The allowlist entry, the DNS answers, the first-request gate, the authorisation reference,
    both caps and the key variable are required for a real profile exactly as in ``tracer
    simulate``; each refusal happens before the key is read or a request is sent; with all of them
    the key goes to the provider in the ``Authorization`` header and nowhere else."""
    files = write_inputs(tmp_path, scenario=two_answers())
    jf = public_files(files)
    first = run_sim(
        files,
        add=public_add(jf),
        environ={ENV_VAR: CANARY},
        transport=JudgeSim(),
        world_kw={"judge_resolver": resolver(PUBLIC_IP)},
    )
    assert first.code == 0, (first.out, first.err)
    bundle = first.value("bundle")
    base = judge_argv(files.out, bundle, jf, "openai/1")
    full = [
        *base,
        "--judge-allowlist", str(jf.allowlist),
        "--max-judge-requests", "5", "--max-judge-tokens", "100000",
        "--judge-authorisation-reference", REF_TEXT, "--judge-first-request-gate",
    ]  # fmt: skip

    def without(option: str) -> list[str]:
        at = full.index(option)
        return [*full[:at], *full[at + (1 if option == "--judge-first-request-gate" else 2) :]]

    cases = [
        ("--judge-allowlist", "judge_allowlist_missing"),
        ("--judge-first-request-gate", "profile_unverified"),
        ("--judge-authorisation-reference", "judge_authorisation_reference_missing"),
        ("--max-judge-requests", "cap_missing (--max-judge-requests)"),
        ("--max-judge-tokens", "cap_missing (--max-judge-tokens)"),
    ]
    for option, code in cases:
        env, sim = EnvRecorder({ENV_VAR: CANARY}), JudgeSim()
        result = run_cmd(
            without(option), environ=env, transport=sim,
            world_kw={"judge_resolver": resolver(PUBLIC_IP)},
        )  # fmt: skip
        assert (result.code, result.err) == (4, f"error: {code}\n"), option
        assert ENV_VAR not in env.reads
        assert sim.requests == []
    private = run_cmd(
        full, environ={ENV_VAR: CANARY}, transport=JudgeSim(),
        world_kw={"judge_resolver": resolver("10.1.2.3")},
    )  # fmt: skip
    assert (private.code, private.err) == (4, "error: dns_answer_not_public\n")
    missing = run_cmd(
        full, environ={}, transport=JudgeSim(), world_kw={"judge_resolver": resolver(PUBLIC_IP)}
    )
    assert (missing.code, missing.err) == (4, "error: key_missing\n")
    assert bundle_count(files.out) == 1
    sim = JudgeSim(default="disagrees")
    result = run_cmd(
        full, environ={ENV_VAR: CANARY}, transport=sim,
        world_kw={"judge_resolver": resolver(PUBLIC_IP)},
    )  # fmt: skip
    assert result.code == 0, (result.out, result.err)
    assert {r.headers["authorization"] for r in sim.requests} == {f"Bearer {CANARY}"}
    assert len(sim.requests) == 2
    assert bundle_count(files.out) == 2
    assert CANARY not in result.out + result.err + all_text(files.out)
    assert REF_TEXT not in result.out + result.err + all_text(files.out)
    assert result.lines[-1].startswith("judge authorisation reference: sha256 ")


def test_a_judge_directory_open_to_others_is_made_private_with_a_warning(tmp_path: Path) -> None:
    """BL-337 at the commands: an existing ``<out>/judge`` with a group or other bit is made 0700
    and the command says so on stderr in one line that names no path, in ``tracer simulate`` and in
    ``tracer judge``; the user's output directory is not changed; the files are private."""
    files = write_inputs(tmp_path, scenario=two_answers())
    files.out.mkdir(mode=0o755)
    files.out.chmod(0o755)
    (files.out / "judge").mkdir(mode=0o755)
    (files.out / "judge").chmod(0o755)
    jf = write_judge_files(files)
    first = run_sim(files, add=loopback_add(jf), transport=JudgeSim())
    assert first.code == 0, (first.out, first.err)
    warning = [line for line in first.err.splitlines() if line.startswith("warning: ")]
    assert warning == ["warning: the judge directory was open to other users and is now private"]
    assert stat.S_IMODE((files.out / "judge").stat().st_mode) == 0o700
    assert stat.S_IMODE(files.out.stat().st_mode) == 0o755
    run_dir = files.out / "judge" / str(first.bundle().run.id)
    assert stat.S_IMODE(run_dir.stat().st_mode) == 0o700
    assert {stat.S_IMODE(p.stat().st_mode) for p in run_dir.iterdir()} == {0o600}
    # private now: no warning the second time, and the same for a repeat of `tracer judge`
    (files.out / "judge").chmod(0o755)
    again = run_cmd(
        judge_argv(
            files.out, first.value("bundle"), jf, "local_loopback/1", *loopback_judge_extra(jf)
        ),
        transport=JudgeSim(),
    )
    assert again.code == 0, (again.out, again.err)
    assert again.err == "warning: the judge directory was open to other users and is now private\n"
    quiet = run_cmd(
        judge_argv(
            files.out, first.value("bundle"), jf, "local_loopback/1", *loopback_judge_extra(jf)
        ),
        transport=JudgeSim(),
    )
    assert quiet.code == 0
    assert quiet.err == ""


def test_simulate_with_a_replay_engine_reproduces_the_judgements_of_the_recorded_run(
    tmp_path: Path,
) -> None:
    """ADR 0030 rule 19 and invariant I6 at ``tracer simulate``: a run of the same call that
    judges with ``replay:<exchange directory of the first run>`` gets the first run's judgements
    (value, closed document, prompt hash, exchange hash, parameters), sends nothing, claims no usage
    and names no host, path or key variable in its profile document."""
    first_sim = JudgeSim(default="disagrees")
    one, files, _ = _judged(tmp_path / "one", "local_loopback/1", sim=first_sim)
    recorded = one.bundle()
    directory = files.out / "judge" / str(recorded.run.id)
    other = write_inputs(tmp_path / "two", scenario=two_answers())
    replay_jf = write_judge_files(other, config=config_doc("local_loopback/1", None))
    quiet = JudgeSim()
    two = run_sim(other, add=judge_add(replay_jf, f"replay:{directory}"), transport=quiet)
    assert two.code == 0, (two.out, two.err)
    assert quiet.requests == []
    assert two.value("judge usage") == "none"
    old, fresh = judge_results(recorded), judge_results(two.bundle())
    assert list(old) == list(fresh) == ["q0", "q1"]
    for item_id in old:
        assert fresh[item_id].value == old[item_id].value == "disagrees"
        assert judge_doc(fresh[item_id]) == judge_doc(old[item_id])
        assert fresh[item_id].judge == old[item_id].judge
    document = two.bundle().snapshot.metadata["tracer.judge_profile"]
    assert not (other.out / "judge").exists(), "a replayed run writes no exchange file"
    assert (document["profile"], document["host"], document["path"], document["key_env"]) == (
        "local_loopback/1", None, None, None,
    )  # fmt: skip
    # a replay of a directory that is not there is a refusal at step 1, not a request
    steps: list[str] = []
    missing = run_sim(
        write_inputs(tmp_path / "three", scenario=two_answers()),
        add=judge_add(replay_jf, f"replay:{tmp_path / 'nowhere'}"),
        transport=quiet,
        world_kw={"trace": steps.append},
    )
    assert (missing.code, missing.err) == (4, "error: exchange_missing\n")
    assert steps == ["parse_inputs"]
    assert missing.out == ""
    assert quiet.requests == []
