# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P4-J15: exchange files, the ``replay:<dir>`` engine and the ``fake:<scenario>`` engine (ADR
0030 rule 19 and I6; contract docs/phase4/api-surface-batch-j1.md section 12).

Each answered judge request leaves a local, private file (the request messages, the raw reply, the
request id and the status); the store holds only the SHA-256. The replay engine answers from those
files and reproduces the stored verdicts byte for byte without sending anything; the fake engine is
deterministic and sends nothing. No test here opens a socket.
"""

from __future__ import annotations

import hashlib
import importlib.util
import inspect
import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

from evalproviders_p4j_support import (  # type: ignore[import-not-found,unused-ignore]
    CANARY_KEY,
    TEXT_CANARY,
    JudgeRig,
    context,
    core,
    golden_item,
    jmod,
    openai_reply,
    params,
    request,
    verdict_text,
)
from evalproviders_support import json_response  # type: ignore[import-not-found,unused-ignore]
from tracer_evalschema.digest import jcs_bytes
from tracer_evalschema.ids import RunId, TranscriptId, TurnId

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "uv.lock").is_file())
SHA = "0123456789abcdef" * 4
#: the format member, written in two pieces: the repository guard refuses a file that holds it
FORMAT = "tracer.judge_" + "exchange/1"


def exchange_doc(**changes: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "messages": [
            {"role": "system", "content": "system text"},
            {"role": "user", "content": "user text"},
        ],
        "response_body": '{"id":"x"}',
        "request_id": "req-1",
        "status": 200,
    }
    fields.update(changes)
    return core("judgerecords").build_exchange_document(**fields)  # type: ignore[no-any-return]


def window(text: str, *, status: str = "ok") -> Any:
    judgewindow = core("judgewindow")
    return judgewindow.AnswerWindow(
        status=status,
        open_ms=200,
        close_ms=2000,
        text=text,
        turn_seqs=(0,) if text else (),
        turn_ids=(TurnId.new(),) if text else (),
        excluded_turns=0,
        transcript_id=TranscriptId.new(),
    )


def guard_module() -> Any:
    path = ROOT / "scripts" / "guard_private_fixtures.py"
    spec = importlib.util.spec_from_file_location("guard_private_fixtures_for_test", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["guard_private_fixtures_for_test"] = module
    spec.loader.exec_module(module)
    return module


def test_judge_exchange_files_private_no_follow_and_guarded(tmp_path: Path) -> None:
    """Rule 19: ``<out>/judge/<run>/item-<id>-<attempt>.json`` is written with mode 0600 into a
    0700 directory, never over an existing file and never through a link; it is read the same way;
    the repository ignores it and the guard names it."""
    exchange = jmod("judgeexchange")
    run_id = RunId.new()
    directory = exchange.exchange_directory(tmp_path / "out", run_id)
    assert directory == tmp_path / "out" / "judge" / str(run_id)
    assert exchange.exchange_file_name("mr_q00", 1) == "item-mr_q00-1.json"
    store = exchange.ExchangeStore(directory)
    for path in (tmp_path / "out" / "judge", directory):
        assert stat.S_IMODE(path.stat().st_mode) == 0o700, path

    document = exchange_doc()
    sha = store.write("mr_q00", 1, document)
    target = directory / "item-mr_q00-1.json"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert target.read_bytes() == jcs_bytes(document)
    assert sha == hashlib.sha256(target.read_bytes()).hexdigest()
    assert store.read("mr_q00", 1) == document
    # never over an existing file; the first stays as it was
    with pytest.raises(exchange.JudgeExchangeError) as caught:
        store.write("mr_q00", 1, exchange_doc(response_body='{"id":"y"}'))
    assert caught.value.code == "exchange_exists"
    assert str(caught.value) == "exchange_exists"
    assert store.read("mr_q00", 1) == document
    # never through a link: a link planted at the file's name is not written through
    victim = tmp_path / "victim.txt"
    victim.write_text("keep me", "utf-8")
    (directory / "item-mr_q01-1.json").symlink_to(victim)
    with pytest.raises(exchange.JudgeExchangeError):
        store.write("mr_q01", 1, document)
    assert victim.read_text("utf-8") == "keep me"
    with pytest.raises(exchange.JudgeExchangeError) as caught:
        store.read("mr_q01", 1)
    assert caught.value.code == "exchange_not_a_regular_file"
    # reading: a file with a group or other permission bit, a missing file, a bad document
    loose = directory / "item-mr_q02-1.json"
    loose.write_bytes(jcs_bytes(document))
    loose.chmod(0o644)
    with pytest.raises(exchange.JudgeExchangeError) as caught:
        store.read("mr_q02", 1)
    assert caught.value.code == "exchange_mode_too_open"
    with pytest.raises(exchange.JudgeExchangeError) as caught:
        store.read("mr_q09", 2)
    assert caught.value.code == "exchange_missing"
    broken = directory / "item-mr_q03-1.json"
    broken.write_bytes(('{"format":"' + FORMAT + '","extra":1}').encode())
    broken.chmod(0o600)
    with pytest.raises(exchange.JudgeExchangeError) as caught:
        store.read("mr_q03", 1)
    assert caught.value.code == "exchange_invalid"
    huge = directory / "item-mr_q04-1.json"
    huge.write_bytes(b" " * 300_000)
    huge.chmod(0o600)
    with pytest.raises(exchange.JudgeExchangeError) as caught:
        store.read("mr_q04", 1)
    assert caught.value.code == "exchange_invalid"
    fifo = directory / "item-mr_q05-1.json"
    os.mkfifo(fifo, 0o600)
    with pytest.raises(exchange.JudgeExchangeError) as caught:
        store.read("mr_q05", 1)
    assert caught.value.code == "exchange_not_a_regular_file"
    # names that could leave the directory, and attempts that are not 1 or 2
    for bad in ("../x", "a/b", "A", "", "q 1", "q-1", "x" * 41, None, 3):
        with pytest.raises(ValueError, match=r"."):
            store.write(bad, 1, document)
    for bad_attempt in (0, 3, -1, True, "1", None):
        with pytest.raises(ValueError, match=r"."):
            store.write("mr_q06", bad_attempt, document)
    # a document that is not a closed tracer.judge_exchange/1 is not written
    with pytest.raises(ValueError, match=r"."):
        store.write("mr_q07", 1, {"format": FORMAT})
    assert not (directory / "item-mr_q07-1.json").exists()

    # the directory: a link, or a directory open to others, is refused
    real = tmp_path / "real-dir"
    real.mkdir(mode=0o700)
    (tmp_path / "dir-link").symlink_to(real, target_is_directory=True)
    with pytest.raises(exchange.JudgeExchangeError) as caught:
        exchange.ExchangeStore(tmp_path / "dir-link")
    assert caught.value.code == "exchange_directory_not_private"
    open_dir = tmp_path / "open-dir"
    open_dir.mkdir()
    open_dir.chmod(0o755)
    with pytest.raises(exchange.JudgeExchangeError) as caught:
        exchange.ExchangeStore(open_dir)
    assert caught.value.code == "exchange_directory_not_private"
    # an existing private directory is used as it is
    assert exchange.ExchangeStore(real).directory == real

    # the engine writes one file per answered request, and none for a refusal of the server
    out = tmp_path / "engine"
    store2 = exchange.ExchangeStore(exchange.exchange_directory(out, run_id))
    rig = JudgeRig(
        tmp_path / "rig",
        [
            openai_reply(content="not json", reply_id="r-1"),
            openai_reply(content=verdict_text(), reply_id="r-2"),
            json_response({"error": {"code": "invalid_request_error", "message": "m"}}, status=400),
            openai_reply(content=None, reply_id="r-3", extra={"choices": []}),
        ],
        exchanges=store2,
    )
    first = rig.complete(request(0, attempt=1))
    second = rig.complete(request(1, attempt=2))
    rejected = rig.complete(request(2, attempt=1, item=golden_item(id="mr_q08")))
    malformed = rig.complete(request(3, attempt=1, item=golden_item(id="mr_q09")))
    files = sorted(p.name for p in store2.directory.iterdir())
    assert files == ["item-mr_q00-1.json", "item-mr_q00-2.json", "item-mr_q09-1.json"], files
    assert rejected.failure is not None
    assert malformed.failure is not None
    saved = store2.read("mr_q00", 1)
    asked = request(0)
    assert [m["content"] for m in saved["messages"]] == [m.content for m in asked.messages]
    assert saved["status"] == 200
    assert saved["request_id"] == "r-1"
    assert json.loads(saved["response_body"])["choices"][0]["message"]["content"] == "not json"
    assert (
        first.exchange_sha256
        == hashlib.sha256((store2.directory / "item-mr_q00-1.json").read_bytes()).hexdigest()
    )
    assert second.exchange_sha256 != first.exchange_sha256
    for path in store2.directory.iterdir():
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        text = path.read_text("utf-8")
        assert CANARY_KEY not in text
        assert "authorization" not in text.lower()

    # guarded: git ignores the files and the repository guard names them
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".gitignore").write_text((ROOT / ".gitignore").read_text("utf-8"), "utf-8")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)  # noqa: S603, S607
    sample = "out/judge/run_01a17f10-4888-7637-86a0-1af7ad452c4e/item-mr_q00-1.json"
    ignored = subprocess.run(  # noqa: S603
        ["git", "-C", str(repo), "check-ignore", "-q", sample],  # noqa: S607
        capture_output=True,
    )
    assert ignored.returncode == 0, "the exchange files are git-ignored"
    guard = guard_module()
    assert guard.kind_by_name(sample) == guard.JUDGE_EXCHANGE == "tracer judge exchange file"
    assert guard.kind_by_name("out/judge/run_x/item-mr_q00-2.json") == guard.JUDGE_EXCHANGE
    assert guard.kind_by_name("docs/judge/notes.json") is None
    assert guard.kind_by_name("packages/x/item-mr_q00-1.json") is None
    renamed = jcs_bytes(document)
    assert guard.kind_by_content("data/anything.json", renamed[:4096]) == guard.JUDGE_EXCHANGE
    assert guard.kind_by_content("data/anything.json", b'{"format":"other/1"}') is None


def _recorded_run(tmp_path: Path) -> tuple[Path, dict[str, Any], list[Any], Any]:
    """Judge three items over a scripted transport and keep every exchange; the third item gets an
    invalid reply first (one regeneration)."""
    exchange = jmod("judgeexchange")
    judge = core("judge")
    items = [
        golden_item(id="mr_q00"),
        golden_item("equivalent_answer", id="mr_q01", scripted_turn_index=1),
        golden_item(id="mr_q02", scripted_turn_index=2),
    ]
    run_id = RunId.new()
    store = exchange.ExchangeStore(exchange.exchange_directory(tmp_path / "out", run_id))
    replies = [
        openai_reply(content=verdict_text(items[0]), reply_id="a-1", model="judge-model-1-x"),
        openai_reply(
            content=json.dumps(
                {"verdict": "disagrees", "reason_code": "meaning_differs", "facts": []}
            ),
            reply_id="a-2",
            model="judge-model-1-x",
        ),
        openai_reply(content="I cannot decide", reply_id="a-3", model="judge-model-1-x"),
        openai_reply(
            content=json.dumps(
                {
                    "verdict": "cannot_tell",
                    "reason_code": "insufficient_information",
                    "facts": [
                        {"index": 0, "status": "cannot_tell"},
                        {"index": 1, "status": "stated"},
                    ],
                }
            ),
            reply_id="a-4",
            model="judge-model-1-x",
        ),
    ]
    rig = JudgeRig(tmp_path / "rig", replies, exchanges=store)
    answers = [
        "\u0909\u0924\u094d\u0924\u0930 \u090f\u0915",
        "\u0909\u0924\u094d\u0924\u0930",
        "\u0905",
    ]
    recorded = {}
    ctx = context()
    index = 0
    for item, answer in zip(items, answers, strict=True):
        judged = judge.judge_item(
            engine=rig.engine,
            item=item,
            window=window(answer),
            params=params(),
            context=ctx,
            first_index=index,
            on_usage=lambda record: None,
        )
        index += judged.attempts
        recorded[item.id] = judged
    return store.directory, recorded, items, answers


def test_replay_judge_reproduces_stored_verdicts_byte_identical(tmp_path: Path) -> None:
    """Invariant I6: the replay engine answers from the stored exchanges and every judgement it
    gives equals the recorded one, down to the bytes of its ``tracer.judge/1`` document; it sends
    nothing, writes nothing, and refuses a request that is not the stored one."""
    exchange = jmod("judgeexchange")
    judge = core("judge")
    records = core("judgerecords")
    directory, recorded, items, answers = _recorded_run(tmp_path)
    profile = jmod("judgeprofiles").get_profile("local_loopback/1")
    assert recorded["mr_q00"].state == "verdict"
    assert recorded["mr_q01"].verdict == "disagrees"
    assert recorded["mr_q02"].state == "cannot_tell"
    assert recorded["mr_q02"].attempts == 2
    before = sorted((p.name, p.read_bytes()) for p in directory.iterdir())
    modes = {p.name: stat.S_IMODE(p.stat().st_mode) for p in directory.iterdir()}

    engine = exchange.create_replay_judge(directory, profile=profile)
    assert isinstance(engine, judge.JudgeEngine)
    ctx = context()
    index = 0
    for item, answer in zip(items, answers, strict=True):
        again = judge.judge_item(
            engine=engine,
            item=item,
            window=window(answer),
            params=params(),
            context=ctx,
            first_index=index,
            on_usage=lambda record: None,
        )
        index += again.attempts
        first = recorded[item.id]
        for field in (
            "item_id",
            "state",
            "verdict",
            "reason_code",
            "facts",
            "error_code",
            "attempts",
            "regenerated",
            "prompt_sha256",
            "exchange_sha256",
            "reported_model",
            "params",
        ):
            assert getattr(again, field) == getattr(first, field), (item.id, field)
        assert jcs_bytes(records.judgement_document(again)) == jcs_bytes(
            records.judgement_document(first)
        ), item.id
        assert again.usage == (), "a replay costs nothing and records no usage"
        assert first.usage, "the recorded run did"
    # nothing was written, nothing was changed
    assert sorted((p.name, p.read_bytes()) for p in directory.iterdir()) == before
    assert {p.name: stat.S_IMODE(p.stat().st_mode) for p in directory.iterdir()} == modes

    # a request that is not the stored one is refused, with a machine reason that quotes nothing
    different = judge.judge_item(
        engine=exchange.create_replay_judge(directory, profile=profile),
        item=items[0],
        window=window("\u0905\u0905"),
        params=params(),
        context=ctx,
        first_index=0,
        on_usage=lambda record: None,
    )
    assert different.state == "error"
    assert different.error_code == "judge_request_failed"
    assert isinstance(different.failure, judge.JudgeResponseInvalid)
    assert different.failure.reason == "replay_request_differs"
    # a request with no stored exchange
    other_item = golden_item(id="mr_q77", scripted_turn_index=7)
    missing = judge.judge_item(
        engine=exchange.create_replay_judge(directory, profile=profile),
        item=other_item,
        window=window("\u0905"),
        params=params(),
        context=ctx,
        first_index=0,
        on_usage=lambda record: None,
    )
    assert isinstance(missing.failure, judge.JudgeResponseInvalid)
    assert missing.failure.reason == "replay_missing"
    # a tampered file (the stored messages edited) is a different request
    tampered = json.loads((directory / "item-mr_q00-1.json").read_text("utf-8"))
    tampered["messages"][1]["content"] += " "
    (directory / "item-mr_q00-1.json").write_bytes(jcs_bytes(tampered))
    again = judge.judge_item(
        engine=exchange.create_replay_judge(directory, profile=profile),
        item=items[0],
        window=window(answers[0]),
        params=params(),
        context=ctx,
        first_index=0,
        on_usage=lambda record: None,
    )
    assert again.failure is not None
    assert again.failure.reason == "replay_request_differs"
    # the engine takes a directory and a profile (to say what would have been sent): it cannot send
    assert list(inspect.signature(exchange.create_replay_judge).parameters) == [
        "directory",
        "profile",
    ]
    for name in ("transport", "api_key", "url", "sleep"):
        assert name not in inspect.signature(exchange.ReplayJudge.__init__).parameters
    with pytest.raises(exchange.JudgeExchangeError):
        exchange.create_replay_judge(tmp_path / "does-not-exist", profile=profile)


def test_fake_judge_is_deterministic_and_sends_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rule 25: ``fake:<scenario>`` is a closed set of deterministic scenarios that answer from the
    request alone: no client, no socket, no file, no usage, no clock."""
    exchange = jmod("judgeexchange")
    judge = core("judge")
    output = core("judgeoutput")

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("the fake judge reached for the network")

    monkeypatch.setattr(httpx.Client, "__init__", forbidden)
    monkeypatch.setattr(httpx.Client, "send", forbidden)
    assert tuple(exchange.FAKE_SCENARIOS) == (
        "agrees",
        "disagrees",
        "cannot_tell",
        "invalid_once",
        "invalid",
        "fenced",
        "auth_failure",
    )
    for bad in ("", "AGREES", "agrees ", "random", "replay", None):
        with pytest.raises(judge.JudgeConfigurationError) as caught:
            exchange.create_fake_judge(bad)
        assert caught.value.reason == "unknown_scenario"
    ctx = context()
    items = [golden_item(), golden_item("equivalent_answer", id="mr_q01")]
    expected = {
        "agrees": (
            "agrees",
            {"all_facts": "all_facts_stated", "equivalent_answer": "meaning_same"},
        ),
        "disagrees": (
            "disagrees",
            {"all_facts": "fact_absent", "equivalent_answer": "meaning_differs"},
        ),
        "cannot_tell": (
            "cannot_tell",
            {
                "all_facts": "insufficient_information",
                "equivalent_answer": "insufficient_information",
            },
        ),
    }
    for scenario in exchange.FAKE_SCENARIOS:
        engine = exchange.create_fake_judge(scenario)
        assert isinstance(engine, judge.JudgeEngine)
        for item in items:
            asked = request(0, item=item)
            first = engine.complete(asked, ctx)
            twin = exchange.create_fake_judge(scenario).complete(asked, ctx)
            assert first.usage == ()
            assert first.exchange_sha256 is None
            if scenario == "auth_failure":
                assert isinstance(first.failure, judge.JudgeAuthError)
                assert first.text is None
                assert type(twin.failure) is type(first.failure)
                assert str(twin.failure) == str(first.failure)
                continue
            assert first == twin, "the same request gives the same outcome, from any instance"
            assert first.failure is None
            assert first.finish_reason == "stop"
            assert first.reported_model == f"fake/{scenario}"
            if scenario in expected:
                verdict, reasons = expected[scenario]
                got = output.parse_judge_output(
                    first.text, match_mode=item.match_mode, fact_count=len(item.expected_facts)
                )
                assert (got.verdict, got.reason_code) == (verdict, reasons[item.match_mode])
            elif scenario == "invalid_once":
                with pytest.raises(output.JudgeOutputInvalid):
                    output.parse_judge_output(
                        first.text, match_mode=item.match_mode, fact_count=len(item.expected_facts)
                    )
                # the attempt decides, not the position of the request
                assert engine.complete(request(5, attempt=1, item=item), ctx).text == first.text
                regenerated = engine.complete(request(4, attempt=2, item=item), ctx)
                assert (
                    output.parse_judge_output(
                        regenerated.text,
                        match_mode=item.match_mode,
                        fact_count=len(item.expected_facts),
                    ).verdict
                    == "agrees"
                )
            elif scenario == "fenced":
                assert first.text.startswith("```")
                with pytest.raises(output.JudgeOutputInvalid):
                    output.parse_judge_output(
                        first.text, match_mode=item.match_mode, fact_count=len(item.expected_facts)
                    )
                assert json.loads(first.text.strip("`").removeprefix("json"))["verdict"]
            elif scenario == "invalid":
                for attempt in (1, 2):
                    reply = engine.complete(request(attempt, attempt=attempt, item=item), ctx)
                    with pytest.raises(output.JudgeOutputInvalid):
                        output.parse_judge_output(
                            reply.text,
                            match_mode=item.match_mode,
                            fact_count=len(item.expected_facts),
                        )
    # through the driver: the scenarios give the outcomes their names say
    results = {}
    for scenario in exchange.FAKE_SCENARIOS:
        engine = exchange.create_fake_judge(scenario)
        results[scenario] = judge.judge_item(
            engine=engine,
            item=items[0],
            window=window("\u0905"),
            params=params(),
            context=ctx,
            first_index=0,
            on_usage=lambda record: None,
        )
    assert results["agrees"].verdict == "agrees"
    assert results["agrees"].attempts == 1
    assert results["disagrees"].verdict == "disagrees"
    assert results["cannot_tell"].state == "cannot_tell"
    assert (results["invalid_once"].verdict, results["invalid_once"].attempts) == ("agrees", 2)
    assert results["invalid"].error_code == "judge_output_invalid"
    assert results["fenced"].error_code == "judge_output_invalid"
    assert results["auth_failure"].error_code == "judge_auth_failed"
    assert results["auth_failure"].stops_run is True
    # nothing sent and nothing written: the text of the answer reaches no file and no stream
    assert TEXT_CANARY not in repr(results["agrees"].failure)
    # the constructor takes a scenario name and nothing else
    assert list(inspect.signature(exchange.create_fake_judge).parameters) == ["scenario"]
