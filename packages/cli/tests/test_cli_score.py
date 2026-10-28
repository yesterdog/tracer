# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P3-22: the ``tracer score`` command (contract docs/phase3/api-surface-batch-e.md, 1 to 4).

The command runs in-process through ``tracer_cli.cli.run`` with an injected ``Environment``: the
streams, the environment, the clock, the sleep, the HTTP transport and the engine factory are
the test's. The scored call is the hand-computed base scenario of the batch D tests: 11 export
tokens and 11 speech-to-text tokens, H 9, S 1, D 1, I 1, MER 3/12 = 0.25; with the test
threshold 0.3 two spans are flags (count 2, flagged export tokens 1, share 1/11); with the test
review budget 0.25 the share is within the budget. No test opens a socket (autouse guard), reads
a real key or reads the machine's environment.
"""

from __future__ import annotations

import ast
import asyncio
import io
import json
import re
import socket
import stat
from pathlib import Path
from typing import Any

import httpx
import pytest

from cli_batche_support import (  # type: ignore[import-not-found,unused-ignore]
    CLI_SRC,
    DENY_LIST,
    REPORT_NAME,
    ROOT,
    SarvamScript,
    base_bodies,
    cli_mod,
    es,
    fresh,
    load_stored_project,
    load_stored_run,
    price_file_text,
    replay_mod,
    report_files,
    run_cli,
    score_args,
    summary_mod,
    tree_bytes,
    write_inputs,
    write_replay,
)


def _refuse(*args: Any, **kwargs: Any) -> Any:
    raise AssertionError("a unit test tried to use the network (ADR 0024 rule 17)")


async def _refuse_async(*args: Any, **kwargs: Any) -> Any:
    raise AssertionError("a unit test tried to use the network (ADR 0024 rule 17)")


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)
    monkeypatch.setattr(socket, "getaddrinfo", _refuse)
    monkeypatch.setattr(asyncio.BaseEventLoop, "create_connection", _refuse_async)
    monkeypatch.setattr(asyncio.BaseEventLoop, "sock_connect", _refuse_async)
    monkeypatch.delenv("SARVAM_API_KEY", raising=False)


SCORED_LABELS = [
    "result",
    "language declared by user",
    "stt provider",
    "stt model",
    "normaliser",
    "agreement distance (MER)",
    "tokens",
    "flags",
    "flagged share of export tokens",
    "export turns with a flag",
    "review budget",
    "stt usage",
    "cost",
    "ingest warnings",
    "bundle",
    "store",
    "report",
]
HEAD_OPTIONS = ("--declared-language", "--caller-origin", "--consent", "--retention", "--stt")
NOT_SCORED_TAIL = ["stt usage", "cost", "ingest warnings", "bundle", "store", "report"]


def _labels(out: str) -> list[str]:
    return [line.partition(": ")[0] for line in out.splitlines()]


def _score(tmp_path: Path, **kwargs: Any) -> tuple[Any, Any]:
    """Write the inputs and run the command on them; ``options`` and ``extra`` go to the args."""
    run_kwargs = {
        k: kwargs.pop(k)
        for k in ("engine", "environ", "transport", "production", "instant")
        if k in kwargs
    }
    arg_kwargs = {k: kwargs.pop(k) for k in ("options", "extra", "chunking") if k in kwargs}
    inputs = write_inputs(tmp_path, **kwargs)
    return inputs, run_cli(score_args(inputs, **arg_kwargs), **run_kwargs)


def _subparser() -> Any:
    cli = cli_mod()
    parser = cli.build_parser()
    action = next(a for a in parser._actions if a.__class__.__name__ == "_SubParsersAction")
    return action, action.choices["score"]


# ------------------------------------------------------------------ P3-22 required names


def test_cli_requires_every_privacy_and_language_input(tmp_path: Path) -> None:
    # the parser itself: these options are required and none has a default
    _, score = _subparser()
    by_flag = {flag: action for action in score._actions for flag in action.option_strings}
    for flag in (*HEAD_OPTIONS, "--out", "--rubric", "--transcript"):
        action = by_flag[flag]
        assert action.required is True, flag
        assert action.default is None, flag
    usage = score.format_usage()
    for flag in HEAD_OPTIONS:
        assert re.search(rf"\[{re.escape(flag)}(?![-\w])", usage) is None, (
            f"{flag} is shown as optional"
        )
    assert "[--dry-run]" in usage
    # omitting any one of them is a usage error; nothing runs and nothing is created
    for index, flag in enumerate((*HEAD_OPTIONS, "--out", "--rubric", "--transcript")):
        folder = tmp_path / f"case{index}"
        folder.mkdir()
        inputs = write_inputs(folder)
        result = run_cli(score_args(inputs, options={flag: None}))
        assert result.code == 64, (flag, result.err)
        assert flag in result.err, (flag, result.err)
        assert result.out == ""
        assert result.factory_calls == 0
        assert not inputs.out.exists()
    # an environment variable or a config never fills a missing input
    folder = tmp_path / "env"
    folder.mkdir()
    inputs = write_inputs(folder)
    environ = {
        "TRACER_CONSENT": "obtained",
        "TRACER_RETENTION": "keep",
        "TRACER_DECLARED_LANGUAGE": "mr",
        "TRACER_CALLER_ORIGIN": "human",
        "CONSENT": "obtained",
    }
    result = run_cli(score_args(inputs, options={"--consent": None}), environ=environ)
    assert result.code == 64
    assert result.factory_calls == 0
    # a value outside the choices is also a usage error
    bad = {
        "--consent": ["maybe", "", "OBTAINED", "yes"],
        "--caller-origin": ["robot", "", "Human", "agent"],
        "--retention": ["", "forever", "KEEP", "keep:", "delete-after:", "keep-until:nope"],
        "--declared-language": ["", " mr", "mr_IN", "m", "mr in"],
        "--stt": ["", "other", "fake:", "SARVAM", "sarvam "],
    }
    number = 0
    for flag, values in bad.items():
        for value in values:
            number += 1
            inputs = write_inputs(fresh(tmp_path, f"bad{number}"))
            result = run_cli(score_args(inputs, options={flag: value}))
            assert result.code == 64, (flag, value, result.err)
            assert result.factory_calls == 0
            assert not inputs.out.exists()


def test_cli_prints_counts_with_denominators(tmp_path: Path) -> None:
    _, result = _score(tmp_path)
    assert result.code == 0, result.err
    assert result.err == ""
    assert _labels(result.out) == SCORED_LABELS
    assert result.value("result") == "scored"
    assert result.value("language declared by user") == "mr"
    assert result.value("stt provider") == "fake"
    assert result.value("stt model") == "fake-model-1 (as requested)"
    assert result.value("normaliser") == "mr-Deva/1"
    assert result.value("agreement distance (MER)") == "0.25 (3 of 12; H 9, S 1, D 1, I 1)"
    assert result.value("tokens") == "11 in the export, 11 in the stt transcript"
    assert result.value("flags") == "2 (confidence at or above 0.3)"
    share = result.value("flagged share of export tokens")
    assert share == f"1 of 11 ({1 / 11!r})"
    assert result.value("export turns with a flag") == "1 of 3"
    assert result.value("review budget") == "0.25 (flagged share is within it)"
    assert result.value("stt usage") == "2 requests, 3 audio seconds"
    assert result.value("cost") == "not available (no_price_for_model)"
    assert result.value("ingest warnings") == "0"
    # the third span (confidence 0.25) joins the flags when the threshold is lowered to 0.2
    folder = tmp_path / "low"
    folder.mkdir()
    support = es()
    _, low = _score(folder, rubric=support.rubric_text(threshold=0.2))
    assert low.value("flags") == "3 (confidence at or above 0.2)"
    assert low.value("flagged share of export tokens") == f"2 of 11 ({2 / 11!r})"
    assert low.value("export turns with a flag") == "2 of 3"
    assert low.value("agreement distance (MER)") == "0.25 (3 of 12; H 9, S 1, D 1, I 1)"
    # two texts that agree: zero flags, still with their denominators, and no invented value
    folder = tmp_path / "same"
    folder.mkdir()
    texts = [
        " ".join(
            [
                support.NAMASKAR,
                support.MALA,
                support.PAANI,
                support.GAAV,
                support.KAAM,
                support.GHAR,
                support.BAAZAR,
            ]
        ),
        " ".join([support.DON, support.TEEN, support.JEVAN, support.VEL_LLA]),
    ]
    engine = support.FakeEngine(chunk_scripts=support.scripts(texts, phrases=None))
    _, same = _score(folder, engine=engine)
    assert same.code == 0, same.err
    assert same.value("agreement distance (MER)") == "0.0 (0 of 11; H 11, S 0, D 0, I 0)"
    assert same.value("flags") == "0 (confidence at or above 0.3)"
    assert same.value("flagged share of export tokens") == "0 of 11 (0.0)"
    assert same.value("export turns with a flag") == "0 of 3"


def test_cli_output_never_claims_accuracy(tmp_path: Path) -> None:
    cli = cli_mod()
    support = es()
    outputs: list[str] = []
    labelled = 0

    def keep(result: Any, name: str) -> None:
        outputs.extend([result.out, result.err])
        assert not DENY_LIST.search(result.out + result.err), (name, DENY_LIST.findall(result.out))

    scenarios: dict[str, dict[str, Any]] = {
        "scored": {},
        "over budget": {"rubric": support.rubric_text(budget=0.05)},
        "placeholder": {"rubric": support.rubric_text(threshold=0.5, budget=1.0)},
        "not scored: language": {
            "engine": support.FakeEngine(chunk_scripts=support.scripts(languages=["mr", "hi"]))
        },
        "not scored: no export agent line": {
            "export": support.export_text([("user", "KAAM GHAR")])
        },
        "stt failed": {
            "engine": support.FakeEngine(
                fail_at=1, failure=importlib_error("SttServerError", chunk_index=1, status=503)
            )
        },
        "refused rubric": {"rubric": "{}"},
        "dry run": {"extra": ["--dry-run"]},
        "embed warning": {"extra": ["--embed-audio"]},
    }
    for index, (name, kwargs) in enumerate(scenarios.items()):
        folder = tmp_path / f"s{index}"
        folder.mkdir()
        _, result = _score(folder, **kwargs)
        keep(result, name)
        if result.code == 0 and "extra" not in kwargs:
            labelled += 1
            assert "agreement distance (MER)" in result.out
    assert labelled >= 3
    # the help of the program and of the command
    for argv in (["--help"], ["score", "--help"]):
        stream_out, stream_err = io.StringIO(), io.StringIO()
        with pytest.raises(SystemExit) as caught:
            cli.run(argv, _environment(stream_out, stream_err))
        assert caught.value.code == 0
        assert not DENY_LIST.search(stream_out.getvalue() + stream_err.getvalue())
        outputs.append(stream_out.getvalue())
    # no output anywhere calls the number by another name
    joined = "\n".join(outputs).lower()
    for word in ("accuracy", "error rate", "recall", "precision", " wer", " cer"):
        assert word not in joined, word


def importlib_error(name: str, **facts: Any) -> Any:
    import importlib

    return getattr(importlib.import_module("tracer_evalcore.stt"), name)(**facts)


def _environment(out: io.StringIO, err: io.StringIO) -> Any:
    cli = cli_mod()
    return cli.Environment(
        stdout=out,
        stderr=err,
        environ={},
        clock=lambda: es().CREATED_AT,
        sleep=lambda seconds: None,
        transport=None,
        engine_factory=None,
    )


def test_cli_fake_stt_runs_offline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # --stt fake:<dir> replays one recorded Sarvam body per chunk through the real mapping,
    # with no key, no HTTP client, no socket and no call log (contract section 4.2)
    def no_client(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("the replay engine built an HTTP client")

    monkeypatch.setattr(httpx.Client, "__init__", no_client)
    monkeypatch.setattr(httpx.Client, "send", no_client)
    folder = write_replay(tmp_path / "replay", base_bodies())
    inputs = write_inputs(tmp_path)
    args = score_args(inputs, options={"--stt": f"fake:{folder}"})
    result = run_cli(args, production=True, environ={})
    assert result.code == 0, result.err
    assert result.err == ""
    assert result.factory_calls == 0
    assert _labels(result.out) == SCORED_LABELS
    assert result.value("stt provider") == "replay"
    assert result.value("stt model") == "recorded-responses (as requested)"
    assert result.value("agreement distance (MER)") == "0.25 (3 of 12; H 9, S 1, D 1, I 1)"
    assert result.value("flags") == "2 (confidence at or above 0.3)"
    # a replay sends nothing, so it has no usage and no cost: never a request that was not made
    assert result.value("stt usage") == "none"
    assert result.value("cost") == "not available (no_usage)"
    names = sorted(tree_bytes(inputs.out))
    assert "tracer.sqlite" in names
    assert any(REPORT_NAME.fullmatch(n) for n in names)
    assert "sarvam_call_log.jsonl" not in names
    assert len(names) == 2, names
    # the env file is not read for a replay (a path that does not exist is not an error)
    folder2 = tmp_path / "again"
    folder2.mkdir()
    inputs2 = write_inputs(folder2)
    result2 = run_cli(
        score_args(
            inputs2,
            options={"--stt": f"fake:{folder}", "--env-file": str(folder2 / "no-such-file")},
        ),
        production=True,
    )
    assert result2.code == 0, result2.err
    # stored provenance says replay, in the run that was stored
    stored = load_stored_run(inputs.out, result.value("bundle"))
    stt = next(t for t in stored.transcripts if t.source == "our_stt")
    assert (stt.provider, stt.model) == ("replay", "recorded-responses")
    assert stored.usage_records == []


@pytest.mark.parametrize(
    "case",
    [
        "ok",
        "not scored: unsupported language",
        "stt failed",
        "refused: rubric without a budget",
        "refused: missing audio file",
        "refused: language other than mr",
        "refused: no key",
        "refused: bad export",
        "usage: missing option",
        "usage: unknown option",
        "usage: unknown command",
        "usage: no command",
        "unexpected failure",
    ],
)
def test_cli_exit_codes(tmp_path: Path, case: str) -> None:
    cli = cli_mod()
    support = es()
    expected = {
        "ok": 0,
        "not scored: unsupported language": 2,
        "stt failed": 3,
        "refused: rubric without a budget": 4,
        "refused: missing audio file": 4,
        "refused: language other than mr": 4,
        "refused: no key": 4,
        "refused: bad export": 4,
        "usage: missing option": 64,
        "usage: unknown option": 64,
        "usage: unknown command": 64,
        "usage: no command": 64,
        "unexpected failure": 1,
    }[case]
    inputs = write_inputs(tmp_path)
    args = score_args(inputs)
    kwargs: dict[str, Any] = {}
    if case == "not scored: unsupported language":
        kwargs["engine"] = support.FakeEngine(chunk_scripts=support.scripts(languages=["mr", "hi"]))
    elif case == "stt failed":
        kwargs["engine"] = support.FakeEngine(
            fail_at=0, failure=importlib_error("SttRequestRejected", chunk_index=0, status=400)
        )
    elif case == "refused: rubric without a budget":
        inputs.rubric.write_text('{"format": "tracer.rubric/1"}', "utf-8")
    elif case == "refused: missing audio file":
        args[1] = str(tmp_path / "no-such.wav")
    elif case == "refused: language other than mr":
        args = score_args(inputs, options={"--declared-language": "hi"})
    elif case == "refused: no key":
        kwargs.update(production=True, environ={})
    elif case == "refused: bad export":
        inputs.export.write_text("this is not an export\n", "utf-8")
    elif case == "usage: missing option":
        args = score_args(inputs, options={"--consent": None})
    elif case == "usage: unknown option":
        args = [*args, "--api-key", "x"]
    elif case == "usage: unknown command":
        args = ["export", "--out", str(inputs.out)]
    elif case == "usage: no command":
        args = []
    elif case == "unexpected failure":
        kwargs["engine"] = RuntimeError(support.KEY_CANARY + support.TEXT_CANARY)
    result = run_cli(args, **kwargs)
    assert result.code == expected, (case, result.err)
    if expected == 64:
        assert result.out == ""
        assert result.err != ""
    if expected == 1:
        assert support.KEY_CANARY not in result.out + result.err
        assert support.TEXT_CANARY not in result.out + result.err
        assert "RuntimeError" in result.err
        assert "Traceback" not in result.out + result.err
    if expected in (4, 64) and case != "usage: no command":
        assert "Traceback" not in result.err
        assert not inputs.out.exists(), case
    # the table of exit codes is a module constant and the pipeline's own codes are its first three
    assert cli.EXIT_CODES == {
        0: "scored",
        1: "unexpected failure",
        2: "not scored",
        3: "speech-to-text failed",
        4: "input refused",
        64: "usage",
    }
    pipeline = __import__("tracer_evalcore.pipeline", fromlist=["x"])
    assert (cli.EXIT_OK, cli.EXIT_NOT_SCORED, cli.EXIT_STT_FAILED) == (
        pipeline.EXIT_SCORED,
        pipeline.EXIT_NOT_SCORED,
        pipeline.EXIT_STT_FAILED,
    )
    assert (cli.EXIT_UNEXPECTED, cli.EXIT_REFUSED, cli.EXIT_USAGE) == (1, 4, 64)


def test_cli_has_no_export_command(tmp_path: Path) -> None:
    # BL-140 (redaction of shared exports) stays open: no command shares, exports, redacts or
    # uploads a run
    action, _ = _subparser()
    # Phase 4 spec changes (P4-01; batch J part 2: P4-J18 and P4-J21 add judge and labels)
    assert set(action.choices) == {"score", "simulate", "judge", "labels"}
    cli = cli_mod()
    for name in ("export", "share", "redact", "upload", "publish", "report", "bundle"):
        folder = tmp_path / name
        folder.mkdir()
        result = run_cli([name, "--out", str(folder / "o")])
        assert result.code == 64, name
        assert result.out == ""
    parser_text = cli.build_parser().format_help()
    assert "export" not in parser_text.lower().replace("transcript export", "")
    modules = {p.name for p in CLI_SRC.glob("*.py")}
    assert not {"export.py", "share.py", "redact.py", "upload.py"} & modules
    # no option of the score command writes anywhere but --out
    _, score = _subparser()
    flags = {flag for action in score._actions for flag in action.option_strings}
    assert not {f for f in flags if re.fullmatch(r"--(export|share|redact|upload|publish)\w*", f)}


# ------------------------------------------------------------------ outcomes other than scored


def test_cli_not_scored_when_a_chunk_is_not_marathi(tmp_path: Path) -> None:
    support = es()
    engine = support.FakeEngine(chunk_scripts=support.scripts(languages=["mr", "hi"]))
    inputs, result = _score(tmp_path, engine=engine)
    assert result.code == 2
    assert _labels(result.out) == [
        "result",
        "language declared by user",
        "not scored",
        *NOT_SCORED_TAIL,
    ]
    assert result.value("result") == "not scored"
    assert result.value("not scored") == "unsupported language detected"
    # never a number, a flag count or a MER, and never a zero
    assert "agreement distance" not in result.out
    assert "flags:" not in result.out
    assert result.value("stt usage") == "2 requests, 3 audio seconds"
    # the run and the report exist: a call that was not scored is still a fact
    stored = load_stored_run(inputs.out, result.value("bundle"))
    assert {r.status for r in stored.results} == {"error"}
    assert {r.error.code for r in stored.results} == {"stt_unsupported_language"}
    assert len(report_files(inputs.out)) == 1


def test_cli_not_scored_when_the_language_is_not_observed(tmp_path: Path) -> None:
    support = es()
    engine = support.FakeEngine(chunk_scripts=support.scripts(languages=["mr", None]))
    _, result = _score(tmp_path, engine=engine)
    assert result.code == 2
    assert result.value("not scored") == "language not observed in the stt transcript"


def test_cli_not_scored_when_a_side_has_no_token(tmp_path: Path) -> None:
    support = es()
    silent = support.FakeEngine(chunk_scripts=support.scripts(["", ""], phrases=None))
    _, result = _score(tmp_path, engine=silent)
    assert result.code == 2
    assert result.value("not scored") == "no tokens in the stt transcript"
    folder = tmp_path / "export"
    folder.mkdir()
    only_punctuation = support.export_text([("assistant", "... ,,, !!!")])
    _, second = _score(folder, export=only_punctuation)
    assert second.code == 2
    assert second.value("not scored") == "no tokens in the export"


def test_cli_not_scored_when_the_export_has_no_agent_line(tmp_path: Path) -> None:
    support = es()
    export = support.export_text([("user", support.sentence(support.KAAM, support.GHAR))])
    _, result = _score(tmp_path, export=export)
    assert result.code == 2
    assert result.value("not scored") == "missing input agent_transcript"
    assert "agreement distance" not in result.out


def test_cli_stt_failure_exit_3_keeps_the_usage_that_happened(tmp_path: Path) -> None:
    support = es()
    failure = importlib_error("SttServerError", chunk_index=1, status=503, attempts=4)
    engine = support.FakeEngine(fail_at=1, failure=failure)
    inputs, result = _score(tmp_path, engine=engine)
    assert result.code == 3
    assert _labels(result.out) == [
        "result",
        "language declared by user",
        "not scored",
        "detail",
        *NOT_SCORED_TAIL,
    ]
    assert result.value("not scored") == "speech-to-text failed"
    assert result.value("detail") == str(failure)
    # only the answered chunk happened: one request, and its seconds (the cut is the chunker's)
    assert result.value("stt usage").startswith("1 requests, ")
    assert result.value("ingest warnings") == "1"
    stored = load_stored_run(inputs.out, result.value("bundle"))
    assert [w.code for w in stored.run.ingest_warnings] == ["stt_failed"]
    assert {r.status for r in stored.results} == {"not_applicable"}


def test_cli_over_budget_is_not_a_failure_and_no_limit_is_labelled(tmp_path: Path) -> None:
    support = es()
    _, over = _score(tmp_path, rubric=support.rubric_text(budget=0.05))
    assert over.code == 0
    assert over.value("review budget") == "0.05 (flagged share is over it)"
    folder = tmp_path / "placeholder"
    folder.mkdir()
    example = ROOT / "docs" / "examples" / "rubric-mr.example.json"
    _, placeholder = _score(folder, rubric=example.read_bytes())
    assert placeholder.code == 0
    assert placeholder.value("review budget") == "1.0 (no limit)"
    assert placeholder.value("flags") == "2 (confidence at or above 0.5)"
    assert "within" not in placeholder.value("review budget")
    # 1.0 that excludes the bound is a rule that can fail, so it is not labelled "no limit"
    folder = tmp_path / "exclusive"
    folder.mkdir()
    document = support.rubric_doc(threshold=0.5, budget=1.0)
    document["items"][0]["pass_rule"]["inclusive"] = False
    _, exclusive = _score(folder, rubric=json.dumps(document))
    assert exclusive.value("review budget") == "1.0 (flagged share is within it)"
    # an integer 1 in the file is the same placeholder
    folder = tmp_path / "integer"
    folder.mkdir()
    _, integer = _score(folder, rubric=support.rubric_text(threshold=0.5, budget=1))
    assert integer.value("review budget") == "1 (no limit)"


# ------------------------------------------------------------------ writes and records


def test_cli_stores_the_run_and_writes_private_files(tmp_path: Path) -> None:
    inputs, result = _score(tmp_path)
    assert result.code == 0
    out = inputs.out
    assert stat.S_IMODE(out.stat().st_mode) == 0o700
    store_file = out / "tracer.sqlite"
    assert stat.S_IMODE(store_file.stat().st_mode) == 0o600
    (report,) = report_files(out)
    assert report.name == "report-20261025T090000Z.html"
    assert stat.S_IMODE(report.stat().st_mode) == 0o600
    assert result.value("store") == str(store_file)
    assert result.value("report") == str(report)
    assert sorted(p.name for p in out.iterdir()) == sorted(["tracer.sqlite", report.name])
    # nothing is written anywhere else: the folder of the inputs holds the three input files only
    assert sorted(p.name for p in tmp_path.iterdir()) == ["in", "out"]
    assert sorted(p.name for p in (tmp_path / "in").iterdir()) == [
        "call.wav",
        "export.txt",
        "rubric.json",
    ]
    stored = load_stored_run(out, result.value("bundle"))
    values = {r.metric.id: r.value for r in stored.results}
    assert sorted(values.values()) == sorted([0.25, 2, 1 / 11])
    assert stored.run.mode == "recorded"
    assert stored.run.created_at == es().CREATED_AT
    assert stored.run.external_refs == []
    assert stored.run.started_at is None


def test_cli_records_the_privacy_and_language_inputs_as_given(tmp_path: Path) -> None:
    options = {
        "--consent": "obtained",
        "--caller-origin": "simulated",
        "--retention": "delete-after:2027-03-01T12:30:00Z",
    }
    inputs, result = _score(tmp_path, options=options)
    assert result.code == 0, result.err
    stored = load_stored_run(inputs.out, result.value("bundle"))
    assert stored.run.caller_origin == "simulated"
    markers = [a.privacy for a in stored.artifacts] + [t.privacy for t in stored.transcripts]
    assert len(markers) == 3
    for marker in markers:
        assert marker.human_subject is True
        assert marker.consent.status == "obtained"
        assert marker.retention.delete_on_expiry is True
        assert marker.retention.retain_until.isoformat().startswith("2027-03-01T12:30:00")
    export = next(t for t in stored.transcripts if t.source == "agent_export")
    assert export.declared_language.language == "mr"
    assert export.declared_language.source == "user_declared"
    assert export.declared_language.declared_at == es().CREATED_AT
    # the other retention forms
    for index, (value, delete, until) in enumerate(
        [("keep", False, None), ("keep-until:2028-01-01T00:00:00+05:30", False, "2027-12-31")]
    ):
        folder = tmp_path / f"r{index}"
        folder.mkdir()
        replay = write_replay(folder / "replay", base_bodies())
        got_inputs, got = _score(
            folder,
            options={
                "--retention": value,
                "--consent": "not_obtained",
                "--stt": f"fake:{replay}",
            },
            production=True,
        )
        assert got.code == 0, got.err
        again = load_stored_run(got_inputs.out, got.value("bundle"))
        retention = again.artifacts[0].privacy.retention
        assert retention.delete_on_expiry is delete
        assert (retention.retain_until is None) == (until is None)
        if until is not None:
            assert retention.retain_until.isoformat().startswith("2027-12-31T18:30:00")
        assert again.artifacts[0].privacy.consent.status == "not_obtained"


def test_cli_reads_the_clock_once_and_a_second_run_joins_the_store(tmp_path: Path) -> None:
    support = es()
    inputs, first = _score(tmp_path)
    assert first.code == 0
    assert len(first.clock_calls) == 1
    # the same instant names the same report: refused before anything is sent
    again = run_cli(score_args(inputs))
    assert again.code == 4
    assert again.factory_calls == 0
    assert "report" in again.err
    assert len(report_files(inputs.out)) == 1
    # a later instant is a second run of the same call in the same store (finding F14)
    from datetime import timedelta

    later = support.CREATED_AT + timedelta(seconds=5)
    second = run_cli(score_args(inputs), instant=later)
    assert second.code == 0, second.err
    assert len(report_files(inputs.out)) == 2
    assert first.value("bundle") != second.value("bundle")
    for result in (first, second):
        stored = load_stored_run(inputs.out, result.value("bundle"))
        assert len(stored.results) == 3


def test_cli_refuses_before_it_creates_anything(tmp_path: Path) -> None:
    support = es()
    broken: dict[str, dict[str, Any]] = {
        "rubric not json": {"rubric": "{"},
        "rubric budget missing": {
            "rubric": json.dumps(
                {
                    "format": "tracer.rubric/1",
                    "parameters": {"flag_min_confidence": 0.5, "normaliser": "mr-Deva/1"},
                    "items": [{"metric": "stt_export_flag_count", "required": False}],
                }
            )
        },
        "rubric threshold missing": {
            "rubric": json.dumps(
                {
                    "format": "tracer.rubric/1",
                    "parameters": {"normaliser": "mr-Deva/1"},
                    "items": support.rubric_doc()["items"],
                }
            )
        },
        "export not recognised": {"export": "nothing like an export\n"},
        "export empty": {"export": ""},
        "export with an unparsed line": {"export": support.BASE_EXPORT + "stray line\n"},
        "audio not wav": {"frames": 0},
    }
    expect = {
        "rubric not json": "rubric_not_json",
        "rubric budget missing": "rubric_budget_missing",
        "rubric threshold missing": "rubric_shape",
        "export not recognised": "header_not_recognized",
        "export empty": "empty_input",
        "export with an unparsed line": "unparsed_line",
    }
    for index, (name, kwargs) in enumerate(broken.items()):
        folder = tmp_path / f"b{index}"
        folder.mkdir()
        inputs = write_inputs(folder, **{k: v for k, v in kwargs.items() if k != "frames"})
        if name == "audio not wav":
            inputs.wav.write_bytes(b"RIFF....not really")
        result = run_cli(score_args(inputs))
        assert result.code == 4, (name, result.err)
        assert result.out == ""
        assert result.err.startswith("error: "), result.err
        assert result.err.count("\n") == 1
        if name in expect:
            assert expect[name] in result.err, (name, result.err)
        assert result.factory_calls == 0
        assert not inputs.out.exists(), name
        assert "Traceback" not in result.err
        assert str(folder) not in result.err, name
    # the same text reaches the same refusal with --accept-export-warnings only for the
    # warnings that drop a line; the default refuses
    folder = tmp_path / "warn"
    folder.mkdir()
    export = support.BASE_EXPORT + "stray line\n"
    inputs = write_inputs(folder, export=export)
    accepted = run_cli(score_args(inputs, extra=["--accept-export-warnings"]))
    assert accepted.code == 0, accepted.err
    assert accepted.value("ingest warnings") == "1"


def test_cli_decodes_files_as_utf8_and_caps_their_size(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli = cli_mod()
    assert cli.MAX_CONFIG_FILE_BYTES == 1_048_576
    assert cli.MAX_TRANSCRIPT_FILE_BYTES == 67_108_864
    support = es()
    inputs = write_inputs(tmp_path, export=b"call_id=x\n---\n\xff\xfe broken\n")
    result = run_cli(score_args(inputs))
    assert result.code == 4
    assert "UTF-8" in result.err
    assert not inputs.out.exists()
    # the cap is read when the command runs and is exclusive of the file size itself
    folder = tmp_path / "cap"
    folder.mkdir()
    inputs = write_inputs(folder)
    size = len(support.BASE_EXPORT.encode("utf-8"))
    monkeypatch.setattr(cli, "MAX_TRANSCRIPT_FILE_BYTES", size)
    assert run_cli(score_args(inputs)).code == 0
    folder = tmp_path / "over"
    folder.mkdir()
    inputs = write_inputs(folder)
    monkeypatch.setattr(cli, "MAX_TRANSCRIPT_FILE_BYTES", size - 1)
    refused = run_cli(score_args(inputs))
    assert refused.code == 4
    assert "too large" in refused.err
    assert not inputs.out.exists()
    monkeypatch.undo()
    folder = tmp_path / "rubric"
    folder.mkdir()
    big = " " * (cli.MAX_CONFIG_FILE_BYTES + 1)
    inputs = write_inputs(folder, rubric=support.rubric_text() + big)
    refused = run_cli(score_args(inputs))
    assert refused.code == 4
    assert "too large" in refused.err
    folder = tmp_path / "price"
    folder.mkdir()
    inputs = write_inputs(folder)
    price = folder / "price.json"
    price.write_text(price_file_text() + big, "utf-8")
    refused = run_cli(score_args(inputs, options={"--price-file": str(price)}))
    assert refused.code == 4
    assert "too large" in refused.err


def test_cli_declared_language_must_be_mr_in_this_release(tmp_path: Path) -> None:
    for index, language in enumerate(["hi", "mr-IN", "en", "ta"]):
        folder = tmp_path / f"l{index}"
        folder.mkdir()
        inputs = write_inputs(folder)
        result = run_cli(score_args(inputs, options={"--declared-language": language}))
        assert result.code == 4, (language, result.err)
        assert "language_not_supported" in result.err
        assert result.factory_calls == 0
        assert not inputs.out.exists()


def test_cli_stereo_needs_the_explicit_provider_downmix_opt_in(tmp_path: Path) -> None:
    inputs, refused = _score(tmp_path, channels=2)
    assert refused.code == 4
    assert refused.factory_calls == 0
    assert not inputs.out.exists()
    folder = tmp_path / "optin"
    folder.mkdir()
    _, accepted = _score(folder, channels=2, extra=["--accept-provider-downmix"])
    assert accepted.code == 0, accepted.err
    plan = accepted.engine.calls[0][0]
    assert plan.channels == 2


def test_cli_embed_audio_is_opt_in_and_warns(tmp_path: Path) -> None:
    inputs, plain = _score(tmp_path)
    assert plain.err == ""
    assert b"data:audio" not in report_files(inputs.out)[0].read_bytes()
    folder = tmp_path / "embed"
    folder.mkdir()
    embed_inputs, embedded = _score(folder, extra=["--embed-audio"])
    assert embedded.code == 0
    assert embedded.err == (
        "warning: --embed-audio puts the call audio into the report file; do not share the file\n"
    )
    assert b"data:audio/wav;base64," in report_files(embed_inputs.out)[0].read_bytes()


# ------------------------------------------------------------------ dry run


def test_cli_dry_run_sends_nothing_and_prints_the_plan(tmp_path: Path) -> None:
    support = es()
    inputs, result = _score(
        tmp_path,
        extra=["--dry-run"],
        environ={"SARVAM_API_KEY": support.KEY_CANARY},
        options={"--env-file": str(tmp_path / "no-such-env-file")},
    )
    assert result.code == 0, result.err
    assert result.err == ""
    assert result.lines == [
        "chunks: 2",
        "audio seconds: 3",
        "calls: 2",
        "cost: not available (no USD price supplied)",
        "max attempts per chunk: 4",
        "max chunks: 50 (the plan has 2: within the cap)",
        "dry run: nothing was sent",
    ]
    assert result.factory_calls == 0
    assert not inputs.out.exists()
    assert support.KEY_CANARY not in result.out
    # the same lines come from the batch B plan object: the command adds only the last three
    from tracer_evalcore.audio.chunker import chunk_wav
    from tracer_evalcore.importers.wav import read_wav
    from tracer_evalproviders.dryrun import build_dry_run_plan

    audio = read_wav(
        inputs.wav,
        created_at=support.CREATED_AT,
        consent=support.consent(),
        retention=support.retention(),
    )
    plan = chunk_wav(audio, max_chunk_ms=2000, search_ms=1000)
    assert result.lines[:4] == build_dry_run_plan(plan, model="saaras:v4").render().splitlines()


def test_cli_dry_run_prices_only_from_a_user_price_file(tmp_path: Path) -> None:
    inputs = write_inputs(tmp_path)
    price = tmp_path / "price.json"
    price.write_text(price_file_text(), "utf-8")
    priced = run_cli(
        score_args(
            inputs,
            options={"--price-file": str(price), "--priced-at": "2026-10-25T09:30:00Z"},
            extra=["--dry-run"],
        )
    )
    assert priced.code == 0, priced.err
    assert priced.lines[3] == "cost: 0.0003 USD (computed from the supplied USD price)"
    undated = run_cli(score_args(inputs, options={"--price-file": str(price)}, extra=["--dry-run"]))
    assert undated.lines[3] == "cost: not available (ambiguous_price)"
    no_requests = tmp_path / "no-requests.json"
    no_requests.write_text(price_file_text(with_requests=False), "utf-8")
    partial = run_cli(
        score_args(
            inputs,
            options={"--price-file": str(no_requests), "--priced-at": "2026-10-25T09:30:00Z"},
            extra=["--dry-run"],
        )
    )
    assert partial.lines[3] == "cost: not available (unit_unpriced)"
    assert not inputs.out.exists()


def test_cli_dry_run_still_refuses_what_a_real_run_would_refuse(tmp_path: Path) -> None:
    support = es()
    inputs = write_inputs(tmp_path, rubric="{}")
    result = run_cli(score_args(inputs, extra=["--dry-run"]))
    assert result.code == 4
    assert result.out == ""
    folder = tmp_path / "export"
    folder.mkdir()
    inputs = write_inputs(folder, export="not an export\n")
    result = run_cli(score_args(inputs, extra=["--dry-run"]))
    assert result.code == 4
    assert "header_not_recognized" in result.err
    folder = tmp_path / "usage"
    folder.mkdir()
    inputs = write_inputs(folder)
    missing = run_cli(score_args(inputs, options={"--consent": None}, extra=["--dry-run"]))
    assert missing.code == 64
    assert support.KEY_CANARY not in missing.err


def test_cli_main_dry_run_writes_to_the_real_streams(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cli = cli_mod()
    inputs = write_inputs(tmp_path)
    code = cli.main(score_args(inputs, extra=["--dry-run"]))
    shown = capsys.readouterr()
    assert code == 0
    assert shown.out.splitlines()[0] == "chunks: 2"
    assert shown.err == ""
    assert not inputs.out.exists()


# ----------------------------------------------------------- the Sarvam path (mock transport)


def test_cli_sarvam_path_sends_one_request_per_chunk_and_logs_each_attempt(tmp_path: Path) -> None:
    support = es()
    script = SarvamScript()
    inputs = write_inputs(tmp_path)
    result = run_cli(
        score_args(inputs),
        production=True,
        transport=script.transport(),
        environ={"SARVAM_API_KEY": support.KEY_CANARY},
    )
    assert result.code == 0, result.err
    assert result.err == ""
    assert len(script.requests) == 2
    for request in script.requests:
        assert request.headers["api-subscription-key"] == support.KEY_CANARY
        assert str(request.url) == "https://api.sarvam.ai/speech-to-text"
    assert result.value("stt provider") == "sarvam"
    assert result.value("stt model") == "saaras:v4 (as requested)"
    assert result.value("stt usage") == "2 requests, 3 audio seconds"
    assert result.value("agreement distance (MER)") == "0.25 (3 of 12; H 9, S 1, D 1, I 1)"
    names = sorted(tree_bytes(inputs.out))
    assert "sarvam_call_log.jsonl" in names
    log = (inputs.out / "sarvam_call_log.jsonl").read_text("utf-8").splitlines()
    assert [json.loads(line)["phase"] for line in log] == ["before", "after", "before", "after"]
    assert stat.S_IMODE((inputs.out / "sarvam_call_log.jsonl").stat().st_mode) == 0o600
    stored = load_stored_run(inputs.out, result.value("bundle"))
    assert len(stored.usage_records) == 2
    assert all(u.occurred_at is not None for u in stored.usage_records)
    project_refs = [r for r in stored.usage_records if r.provider == "sarvam"]
    assert len(project_refs) == 2


def test_cli_sarvam_path_prices_from_a_user_file_with_the_response_date(tmp_path: Path) -> None:
    support = es()
    inputs = write_inputs(tmp_path)
    price = tmp_path / "price.json"
    price.write_text(price_file_text(), "utf-8")
    result = run_cli(
        score_args(inputs, options={"--price-file": str(price)}),
        production=True,
        transport=SarvamScript().transport(),
        environ={"SARVAM_API_KEY": support.KEY_CANARY},
    )
    assert result.code == 0, result.err
    assert result.value("cost") == "0.0003 USD (computed from the supplied USD price)"
    # a price file that does not price requests leaves every chunk unpriced: never a partial sum
    folder = tmp_path / "audio-only"
    folder.mkdir()
    inputs2 = write_inputs(folder)
    price2 = folder / "price.json"
    price2.write_text(price_file_text(with_requests=False), "utf-8")
    second = run_cli(
        score_args(inputs2, options={"--price-file": str(price2)}),
        production=True,
        transport=SarvamScript().transport(),
        environ={"SARVAM_API_KEY": support.KEY_CANARY},
    )
    assert second.code == 0
    assert second.value("cost") == "not available (unit_unpriced)"
    assert "0 USD" not in second.out


def test_cli_missing_key_refuses_before_any_request_and_before_the_output_directory(
    tmp_path: Path,
) -> None:
    script = SarvamScript()
    inputs = write_inputs(tmp_path)
    result = run_cli(score_args(inputs), production=True, transport=script.transport(), environ={})
    assert result.code == 4
    assert "key_missing" in result.err
    assert script.requests == []
    assert not inputs.out.exists()
    assert result.out == ""


def test_cli_env_file_is_the_only_key_source(tmp_path: Path) -> None:
    support = es()
    other = support.KEY_CANARY + "-from-the-environment"
    file_key = support.KEY_CANARY + "-from-the-file"
    env_file = tmp_path / "keys.env"
    env_file.write_text(
        f"# a comment\nOTHER_SECRET=do-not-read\nexport SARVAM_API_KEY='{file_key}'\n", "utf-8"
    )
    script = SarvamScript()
    inputs = write_inputs(tmp_path)
    result = run_cli(
        score_args(inputs, options={"--env-file": str(env_file)}),
        production=True,
        transport=script.transport(),
        environ={"SARVAM_API_KEY": other},
    )
    assert result.code == 0, result.err
    assert {r.headers["api-subscription-key"] for r in script.requests} == {file_key}
    seen = (
        result.out
        + result.err
        + "".join(v.decode("utf-8", "replace") for v in tree_bytes(inputs.out).values())
    )
    for secret in (other, file_key, "do-not-read", str(env_file)):
        assert secret not in seen, secret
    # a file without the variable is a missing key: the environment is not a fallback
    folder = tmp_path / "nokey"
    folder.mkdir()
    empty = folder / "empty.env"
    empty.write_text("OTHER=1\n", "utf-8")
    inputs2 = write_inputs(folder)
    script2 = SarvamScript()
    refused = run_cli(
        score_args(inputs2, options={"--env-file": str(empty)}),
        production=True,
        transport=script2.transport(),
        environ={"SARVAM_API_KEY": other},
    )
    assert refused.code == 4
    assert "key_missing" in refused.err
    assert script2.requests == []
    assert not inputs2.out.exists()
    # two definitions are ambiguous, an unreadable file is refused, and neither echoes anything
    for index, (content, reason) in enumerate(
        [
            (f"SARVAM_API_KEY={other}\nSARVAM_API_KEY={file_key}\n", "key_ambiguous"),
            (None, "env_file_unreadable"),
        ]
    ):
        sub = tmp_path / f"e{index}"
        sub.mkdir()
        path = sub / "k.env"
        if content is not None:
            path.write_text(content, "utf-8")
        inputs3 = write_inputs(sub)
        got = run_cli(
            score_args(inputs3, options={"--env-file": str(path)}),
            production=True,
            transport=SarvamScript().transport(),
            environ={},
        )
        assert got.code == 4, got.err
        assert reason in got.err
        assert other not in got.err + got.out
        assert file_key not in got.err + got.out
        assert not inputs3.out.exists()


def test_cli_sarvam_rejection_is_exit_3_with_the_usage_that_happened(tmp_path: Path) -> None:
    support = es()
    script = SarvamScript([base_bodies()[0], 400])
    inputs = write_inputs(tmp_path)
    result = run_cli(
        score_args(inputs),
        production=True,
        transport=script.transport(),
        environ={"SARVAM_API_KEY": support.KEY_CANARY},
    )
    assert result.code == 3, result.err
    assert result.value("not scored") == "speech-to-text failed"
    assert "SttRequestRejected" in result.value("detail")
    assert result.value("stt usage").startswith("1 requests, ")
    assert len(script.requests) == 2
    log = (inputs.out / "sarvam_call_log.jsonl").read_text("utf-8").splitlines()
    assert len(log) == 4
    assert support.KEY_CANARY not in result.out + result.err
    stored = load_stored_run(inputs.out, result.value("bundle"))
    assert [w.code for w in stored.run.ingest_warnings] == ["stt_failed"]


# ------------------------------------------------------------------ the replay engine


def test_replay_directory_rules(tmp_path: Path) -> None:
    support = es()
    # a missing chunk file is a failed transcription: exit 3, all or nothing
    folder = write_replay(tmp_path / "r1", base_bodies()[:1])
    inputs = write_inputs(fresh(tmp_path, "a"))
    result = run_cli(score_args(inputs, options={"--stt": f"fake:{folder}"}), production=True)
    assert result.code == 3
    assert result.value("not scored") == "speech-to-text failed"
    assert "replay_file_missing" in result.value("detail")
    # an unreadable body is a failed transcription too, with the reason of the shape check
    bad = write_replay(tmp_path / "r2", [base_bodies()[0], b"not json"])
    inputs2 = write_inputs(fresh(tmp_path, "b"))
    second = run_cli(score_args(inputs2, options={"--stt": f"fake:{bad}"}), production=True)
    assert second.code == 3
    assert "not_json" in second.value("detail")
    # more chunk files than chunks: a stale directory is refused before anything is written
    extra = write_replay(tmp_path / "r3", [*base_bodies(), base_bodies()[0]])
    (tmp_path / "c").mkdir()
    inputs3 = write_inputs(tmp_path / "c")
    third = run_cli(score_args(inputs3, options={"--stt": f"fake:{extra}"}), production=True)
    assert third.code == 4
    assert "replay_extra_file" in third.err
    assert not inputs3.out.exists()
    # a directory that does not exist is refused; so is an empty name
    (tmp_path / "d").mkdir()
    inputs4 = write_inputs(tmp_path / "d")
    fourth = run_cli(
        score_args(inputs4, options={"--stt": f"fake:{tmp_path / 'nope'}"}), production=True
    )
    assert fourth.code == 4
    assert "replay_directory_unreadable" in fourth.err
    (tmp_path / "e").mkdir()
    inputs5 = write_inputs(tmp_path / "e")
    assert run_cli(score_args(inputs5, options={"--stt": "fake:"}), production=True).code == 64
    assert run_cli(score_args(inputs5, options={"--stt": "other"}), production=True).code == 64
    # other files in the directory are ignored
    ignored = write_replay(tmp_path / "r4", base_bodies())
    (ignored / "notes.txt").write_text("hand notes", "utf-8")
    for odd in ("chunk-12345.json", "chunk-1.json", "chunk-0000.json.bak"):
        (ignored / odd).write_text("not a body", "utf-8")
    (tmp_path / "f").mkdir()
    inputs6 = write_inputs(tmp_path / "f")
    ok = run_cli(score_args(inputs6, options={"--stt": f"fake:{ignored}"}), production=True)
    assert ok.code == 0, ok.err
    # the language of a recorded body is read as the provider's tag: a non-mr tag is not scored
    other = write_replay(tmp_path / "r5", base_bodies(["mr-IN", "hi-IN"]))
    (tmp_path / "g").mkdir()
    inputs7 = write_inputs(tmp_path / "g")
    hindi = run_cli(score_args(inputs7, options={"--stt": f"fake:{other}"}), production=True)
    assert hindi.code == 2
    assert hindi.value("not scored") == "unsupported language detected"
    assert support.TEXT_CANARY not in hindi.out + hindi.err


def test_replay_engine_is_an_stt_engine_without_a_network(tmp_path: Path) -> None:
    support = es()
    replay = replay_mod()
    folder = write_replay(tmp_path / "r", base_bodies())
    engine = replay.ReplayStt(folder)
    from tracer_evalcore.stt import SttEngine

    assert isinstance(engine, SttEngine)
    assert replay.REPLAY_FILE_FORMAT == "chunk-{index:04d}.json"
    assert (replay.REPLAY_PROVIDER, replay.REPLAY_MODEL) == ("replay", "recorded-responses")
    run = support.run_pipeline(tmp_path, engine=engine, config=None)
    assert run.result.outcome == "scored"
    assert run.bundle.usage_records == []
    sources = {t.provider for t in run.bundle.transcripts if t.source == "our_stt"}
    assert sources == {"replay"}


# ------------------------------------------------------------------ help, defaults, sources


def test_cli_help_documents_exit_codes_the_price_rule_and_the_key_source() -> None:
    cli = cli_mod()
    out, err = io.StringIO(), io.StringIO()
    with pytest.raises(SystemExit) as caught:
        cli.run(["score", "--help"], _environment(out, err))
    assert caught.value.code == 0
    text = out.getvalue()
    flat = " ".join(text.split())
    assert "must also price requests" in flat
    assert "audio_input_seconds" in flat
    codes = dict(re.findall(r"^\s+(\d+)\s{2,}(\S.*)$", text, flags=re.MULTILINE))
    assert codes == {
        "0": "scored",
        "1": "unexpected failure",
        "2": "not scored",
        "3": "speech-to-text failed",
        "4": "input refused",
        "64": "usage",
    }
    assert "SARVAM_API_KEY" in text
    assert "only" in flat
    assert "--api-key" not in text
    # the privacy and language inputs say they are required, and none is given a default
    for flag in (*HEAD_OPTIONS, "--out"):
        block = re.search(rf"(?ms)^  {flag}\b.*?(?=^  --|^exit codes:|\Z)", text)
        assert block is not None, flag
        assert "required" in block.group(0).lower(), flag
        assert "default" not in block.group(0).lower(), flag
    for flag in ("--max-chunk-ms", "--search-ms", "--stt"):
        assert flag in text


def _root_name(node: ast.expr) -> str | None:
    """The name at the root of a dotted expression: ``datetime`` for ``datetime.datetime``."""
    while isinstance(node, ast.Attribute):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


def test_cli_has_no_key_option_and_reads_no_environment_but_in_environment_system() -> None:
    # the source rules of contract section 1.4 (AST): the process environment and the clock are
    # read in one place, Environment.system; no network module is imported by the command
    forbidden = {"socket", "ssl", "http", "urllib", "httpx", "requests"}
    for path in sorted(CLI_SRC.glob("*.py")):
        tree = ast.parse(path.read_text("utf-8"))
        parents: dict[ast.AST, ast.AST] = {}
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                parents[child] = node
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                names = {(node.module or "").split(".")[0]}
            else:
                names = set()
            if path.name != "report.py":
                assert not names & forbidden, (path.name, names & forbidden)
            elif names & {"httpx", "socket", "ssl", "http", "requests"}:
                pytest.fail("report.py imports a network module")
            reads_env = (
                isinstance(node, ast.Attribute)
                and node.attr in ("environ", "getenv", "putenv", "unsetenv")
                and isinstance(node.value, ast.Name)
                and node.value.id == "os"
            )
            reads_clock = (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("now", "utcnow", "today", "time", "monotonic")
                and _root_name(node.func.value) in ("datetime", "time", "date")
            )
            imports_env = (
                isinstance(node, ast.ImportFrom)
                and node.module == "os"
                and any(a.name in ("environ", "getenv", "putenv") for a in node.names)
            )
            if reads_env or reads_clock or imports_env:
                holder: ast.AST | None = node
                names_up: list[str] = []
                while holder is not None:
                    if isinstance(holder, ast.FunctionDef | ast.ClassDef):
                        names_up.append(holder.name)
                    holder = parents.get(holder)
                where = (path.name, getattr(node, "lineno", 0), names_up)
                assert path.name == "cli.py", where
                assert "system" in names_up, where
    assert "tracer_cli.report" not in (CLI_SRC / "store.py").read_text("utf-8")


def test_summary_is_what_the_command_prints_for_the_run(tmp_path: Path) -> None:
    support = es()
    summary = summary_mod()
    run = support.run_pipeline(tmp_path, config=support.configuration())
    text = summary.render_summary(
        run.bundle, outcome=run.result.outcome, stt_failure=run.result.stt_failure
    )
    lines = text.splitlines()
    assert [line.partition(": ")[0] for line in lines] == SCORED_LABELS[:-3]
    assert lines[0] == "result: scored"
    assert text.endswith("\n")
    _, printed = _score(fresh(tmp_path, "x"))
    # the command adds the cost the user's price file (or its absence) decides; the bundle of the
    # bare pipeline holds no cost record, which the summary says in its own words
    assert lines[12] == "cost: not available (no cost record)"
    assert printed.out.splitlines()[:12] == lines[:12]
    assert printed.out.splitlines()[13] == lines[13]


# ------------------------------------------------------------------ more of the command's rules


class _Closing:
    """An engine with a ``close`` method, which the command must call exactly once."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.closed = 0

    def transcribe(self, plan: Any, context: Any) -> Any:
        return self.inner.transcribe(plan, context)

    def close(self) -> None:
        self.closed += 1


def test_cli_engine_request_carries_the_run_inputs(tmp_path: Path) -> None:
    cli = cli_mod()
    env_file = tmp_path / "keys.env"
    inputs, result = _score(tmp_path, options={"--env-file": str(env_file), "--stt": "fake:/x/y"})
    assert result.code == 0, result.err
    (request,) = result.requests
    assert isinstance(request, cli.EngineRequest)
    assert request.stt == "fake:/x/y"
    assert request.out_dir == inputs.out
    assert request.model == "saaras:v4"
    assert request.environ == {}
    assert request.env_file == env_file
    assert callable(request.clock)
    assert callable(request.sleep)
    assert request.transport is None
    folder = fresh(tmp_path, "default")
    _, plain = _score(folder)
    assert plain.requests[0].stt == "sarvam"
    assert plain.requests[0].env_file is None


def test_cli_closes_the_engine_once_whatever_happens(tmp_path: Path) -> None:
    support = es()
    good = _Closing(support.FakeEngine())
    _, result = _score(tmp_path, engine=good)
    assert result.code == 0
    assert good.closed == 1
    bad = _Closing(support.FakeEngine(raises=RuntimeError("boom")))
    _, failed = _score(fresh(tmp_path, "bad"), engine=bad)
    assert failed.code == 1
    assert bad.closed == 1


def test_cli_clock_must_be_aware_and_names_the_report_in_utc(tmp_path: Path) -> None:
    from datetime import datetime, timedelta, timezone

    inputs = write_inputs(tmp_path)
    naive = run_cli(score_args(inputs), instant=datetime(2026, 10, 25, 9, 0, 0))
    assert naive.code == 1
    assert "ValueError" in naive.err
    assert naive.factory_calls == 0
    assert not inputs.out.exists()
    plus = timezone(timedelta(hours=5, minutes=30))
    shifted = run_cli(score_args(inputs), instant=datetime(2026, 10, 25, 14, 30, 0, tzinfo=plus))
    assert shifted.code == 0, shifted.err
    assert report_files(inputs.out)[0].name == "report-20261025T090000Z.html"
    stored = load_stored_run(inputs.out, shifted.value("bundle"))
    assert stored.run.created_at == es().CREATED_AT


def test_cli_relative_paths_are_read_from_the_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inputs = write_inputs(tmp_path)
    monkeypatch.chdir(tmp_path)
    args = score_args(
        inputs,
        options={
            "--transcript": "in/export.txt",
            "--rubric": "in/rubric.json",
            "--out": "out",
        },
    )
    args[1] = "in/call.wav"
    result = run_cli(args)
    assert result.code == 0, result.err
    assert result.value("store") == str(Path("out") / "tracer.sqlite")
    assert result.value("report") == str(Path("out") / "report-20261025T090000Z.html")
    assert 'src="../in/call.wav"' in the_report_text(inputs.out)


def the_report_text(out: Path) -> str:
    return str(report_files(out)[0].read_text("utf-8"))


def test_cli_chunking_parameters_are_checked_before_anything_is_written(tmp_path: Path) -> None:
    cases = {
        "not a number": ("--max-chunk-ms", "abc", 64),
        "float": ("--search-ms", "1.5", 64),
        "zero window": ("--max-chunk-ms", "0", 4),
        "negative window": ("--max-chunk-ms", "-5", 4),
        "search longer than window": ("--search-ms", "5000", 4),
        "zero search": ("--search-ms", "0", 4),
    }
    for index, (name, (flag, value, code)) in enumerate(cases.items()):
        inputs = write_inputs(fresh(tmp_path, f"c{index}"))
        result = run_cli(score_args(inputs, options={flag: value}))
        assert result.code == code, (name, result.err)
        assert result.factory_calls == 0
        assert not inputs.out.exists(), name
    # the documented defaults apply when the options are left out
    inputs = write_inputs(fresh(tmp_path, "defaults"))
    dry = run_cli(score_args(inputs, chunking=False, extra=["--dry-run"]))
    assert dry.code == 0
    assert dry.lines[0] == "chunks: 1"


def test_cli_price_options_are_checked(tmp_path: Path) -> None:
    inputs = write_inputs(tmp_path)
    alone = run_cli(score_args(inputs, options={"--priced-at": "2026-10-25T09:30:00Z"}))
    assert alone.code == 64
    assert "--priced-at" in alone.err
    for index, value in enumerate(["", "2026-10-25", "2026-10-25 09:30:00Z", "yesterday", "09:30"]):
        got_inputs = write_inputs(fresh(tmp_path, f"p{index}"))
        price = got_inputs.root / "price.json"
        price.write_text(price_file_text(), "utf-8")
        got = run_cli(
            score_args(got_inputs, options={"--price-file": str(price), "--priced-at": value})
        )
        assert got.code == 64, (value, got.err)
        assert not got_inputs.out.exists()
    # a price file the schema refuses is refused with its short code, and nothing is written
    support = es()
    bad_files = {
        "catalog_not_json": "not json",
        "catalog_shape": json.dumps({"entries": []}),
        "catalog_entry_invalid": price_file_text().replace('"USD"', '"INR"'),
        "catalog_entry_not_user_supplied": price_file_text().replace("user_supplied", "catalog"),
    }
    for index, (code, text) in enumerate(bad_files.items()):
        got_inputs = write_inputs(fresh(tmp_path, f"f{index}"))
        price = got_inputs.root / "price.json"
        price.write_text(text, "utf-8")
        got = run_cli(score_args(got_inputs, options={"--price-file": str(price)}))
        assert got.code == 4, (code, got.err)
        assert code in got.err, (code, got.err)
        assert got.factory_calls == 0
        assert not got_inputs.out.exists()
        assert support.TEXT_CANARY not in got.err


def test_cli_replay_has_no_cost_even_with_a_price_file(tmp_path: Path) -> None:
    folder = write_replay(tmp_path / "replay", base_bodies())
    inputs = write_inputs(fresh(tmp_path, "run"))
    price = inputs.root / "price.json"
    price.write_text(price_file_text(), "utf-8")
    result = run_cli(
        score_args(
            inputs,
            options={
                "--stt": f"fake:{folder}",
                "--price-file": str(price),
                "--priced-at": "2026-10-25T09:30:00Z",
            },
        ),
        production=True,
    )
    assert result.code == 0, result.err
    assert result.value("stt usage") == "none"
    assert result.value("cost") == "not available (no_usage)"


def test_cli_dry_run_embed_flag_embeds_nothing_and_says_nothing(tmp_path: Path) -> None:
    inputs = write_inputs(tmp_path)
    result = run_cli(score_args(inputs, extra=["--dry-run", "--embed-audio"]))
    assert result.code == 0
    assert result.err == ""
    assert not inputs.out.exists()


def test_cli_stdout_is_ascii_text_without_the_call(tmp_path: Path) -> None:
    support = es()
    export = support.export_text([*support.EXPORT_LINES, ("assistant", support.TEXT_CANARY)])
    inputs, result = _score(tmp_path, export=export)
    assert result.code == 0
    assert result.out.isascii()
    assert result.err.isascii()
    for word in (support.NAMASKAR, support.TEXT_CANARY, support.CALL_ID):
        assert word not in result.out + result.err
    # the files are not the place for it either: the call text is in the store and the report only
    assert support.TEXT_CANARY.encode() in (inputs.out / "tracer.sqlite").read_bytes()


def test_cli_not_scored_reasons_for_missing_provenance_and_a_refused_document(
    tmp_path: Path,
) -> None:
    support = es()
    summary = summary_mod()
    nameless = support.FakeEngine(provider=None)
    inputs, result = _score(tmp_path, engine=nameless)
    assert result.code == 2
    assert result.value("not scored") == "speech-to-text provenance missing"
    stored = load_stored_run(inputs.out, result.value("bundle"))
    assert summary.not_scored_reasons(stored) == ("speech-to-text provenance missing",)
    model = "sk-" + "A1b2C3d4E5f6G7h8I9j0K1l2M3"
    _, refused = _score(fresh(tmp_path, "model"), engine=support.FakeEngine(model=model))
    assert refused.code == 2
    assert refused.value("not scored") == "flags document refused"
    assert model not in refused.out + refused.err


def test_cli_project_names_the_key_and_never_holds_it(tmp_path: Path) -> None:
    support = es()
    inputs = write_inputs(tmp_path)
    result = run_cli(
        score_args(inputs),
        production=True,
        transport=SarvamScript().transport(),
        environ={"SARVAM_API_KEY": support.KEY_CANARY},
    )
    assert result.code == 0, result.err
    stored = load_stored_run(inputs.out, result.value("bundle"))
    project = load_stored_project(inputs.out, stored.run.project_id)
    refs = [(c.provider, c.model, c.config_ref) for c in project.provider_configs]
    assert refs == [("sarvam", "saaras:v4", "env:SARVAM_API_KEY")]
    # a replay names no provider key: it used none
    folder = write_replay(tmp_path / "replay", base_bodies())
    second_inputs = write_inputs(fresh(tmp_path, "second"))
    replay = run_cli(
        score_args(second_inputs, options={"--stt": f"fake:{folder}"}), production=True
    )
    assert replay.code == 0, replay.err
    stored2 = load_stored_run(second_inputs.out, replay.value("bundle"))
    assert load_stored_project(second_inputs.out, stored2.run.project_id).provider_configs == []


def test_cli_program_options_are_not_abbreviated_either() -> None:
    result = run_cli(["--he"])
    assert result.code == 64
    assert result.out == ""


def test_cli_retries_wait_with_the_injected_sleep(tmp_path: Path) -> None:
    support = es()
    bodies = [503, *base_bodies()]
    script = SarvamScript(bodies)
    inputs = write_inputs(tmp_path)
    result = run_cli(
        score_args(inputs),
        production=True,
        transport=script.transport(),
        environ={"SARVAM_API_KEY": support.KEY_CANARY},
    )
    assert result.code == 0, result.err
    assert len(script.requests) == 3
    assert result.sleeps == [1]
    log = (inputs.out / "sarvam_call_log.jsonl").read_text("utf-8").splitlines()
    assert len(log) == 6


def test_cli_priced_at_dates_a_price_when_the_response_has_no_date(tmp_path: Path) -> None:
    support = es()
    price_text = price_file_text()
    cases = {
        "no date, no priced-at": (None, None, "not available (ambiguous_price)"),
        "no date, priced-at": (
            None,
            "2026-10-25T09:30:00Z",
            "0.0003 USD (computed from the supplied USD price)",
        ),
    }
    for index, (name, (date, priced_at, cost)) in enumerate(cases.items()):
        inputs = write_inputs(fresh(tmp_path, f"d{index}"))
        price = inputs.root / "price.json"
        price.write_text(price_text, "utf-8")
        options = {"--price-file": str(price)}
        if priced_at is not None:
            options["--priced-at"] = priced_at
        result = run_cli(
            score_args(inputs, options=options),
            production=True,
            transport=SarvamScript(date=date).transport(),
            environ={"SARVAM_API_KEY": support.KEY_CANARY},
        )
        assert result.code == 0, (name, result.err)
        assert result.value("cost") == cost, name


def test_cli_a_price_that_starts_mid_run_is_a_partial_cost_never_a_complete_one(
    tmp_path: Path,
) -> None:
    support = es()
    inputs = write_inputs(tmp_path)
    price = tmp_path / "price.json"
    price.write_text(price_file_text(effective_from="2026-10-25T09:30:00.000000Z"), "utf-8")
    script = SarvamScript(dates=["Sun, 25 Oct 2026 09:00:00 GMT", "Sun, 25 Oct 2026 10:00:00 GMT"])
    result = run_cli(
        score_args(inputs, options={"--price-file": str(price)}),
        production=True,
        transport=script.transport(),
        environ={"SARVAM_API_KEY": support.KEY_CANARY},
    )
    assert result.code == 0, result.err
    cost = result.value("cost")
    assert re.fullmatch(r"partial: [0-9.]+ USD \(1 of 2 usage records priced\)", cost), cost
    assert "computed" not in cost


def test_cli_the_report_reference_follows_the_files_where_they_really_are(tmp_path: Path) -> None:
    inputs = write_inputs(tmp_path)
    link = tmp_path / "link-to-in"
    link.symlink_to(inputs.root / "in", target_is_directory=True)
    args = score_args(inputs)
    args[1] = str(link / "call.wav")
    result = run_cli(args)
    assert result.code == 0, result.err
    assert 'src="../in/call.wav"' in the_report_text(inputs.out)


def test_cli_unreadable_inputs_name_the_role_and_the_exception_class_only(tmp_path: Path) -> None:
    inputs = write_inputs(tmp_path)
    missing = str(tmp_path / "no-such-file-name.txt")
    cases = [
        ("transcript", {"--transcript": missing}, "FileNotFoundError"),
        ("rubric", {"--rubric": missing}, "FileNotFoundError"),
        ("price", {"--price-file": missing}, "FileNotFoundError"),
        ("rubric", {"--rubric": str(tmp_path)}, "IsADirectoryError"),
        ("transcript", {"--transcript": str(tmp_path)}, "IsADirectoryError"),
    ]
    for role, options, exception in cases:
        result = run_cli(score_args(inputs, options=options))
        assert result.code == 4
        assert result.err == f"error: cannot read the {role} file ({exception})\n"
        assert result.out == ""
        assert not inputs.out.exists()
    args = score_args(inputs)
    args[1] = missing
    result = run_cli(args)
    assert result.code == 4
    assert result.err == "error: cannot read the audio file (FileNotFoundError)\n"


def _defined_tests() -> set[str]:
    names: set[str] = set()
    for path in ROOT.glob("packages/*/tests/test_*.py"):
        for node in ast.walk(ast.parse(path.read_text("utf-8"))):
            if isinstance(node, ast.FunctionDef):
                names.add(node.name)
    return names


@pytest.mark.parametrize("task", ["P3-22", "P3-23", "P3-24"])
def test_every_test_named_by_the_scope_rows_of_batch_e_exists(task: str) -> None:
    rows = [
        line
        for line in (ROOT / "docs" / "phase3" / "SCOPE.md").read_text("utf-8").splitlines()
        if line.startswith(f"| {task} |")
    ]
    assert len(rows) == 1
    named = set(re.findall(r"`(test_\w+)`", rows[0]))
    assert len(named) >= 1
    assert named <= _defined_tests(), sorted(named - _defined_tests())
    if task == "P3-22":
        assert len(named) >= 6
    if task == "P3-23":
        assert len(named) >= 10
