# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P4-12, the call log of a simulated run (contract docs/phase4/api-surface-batch-b.md section 6;
ADR 0024 amendment A8 and invariant IA4, rule 15 for the file rules).

One file, ``sim_call_log.jsonl``, for every engine of the call: two lines per attempt (``before``,
on disk and flushed before the request is sent, and ``after``) with the stage, the request index,
the attempt, the bytes, the characters (TTS) or audio seconds (STT) sent, the HTTP status or the
transport error kind, the elapsed milliseconds and the request id; one ``connect`` line and one
``end`` line (the end reason and counts). No text, no number (a phone number), no URL, no header,
no model or voice name and no key. Mode 0600, directory 0700 for a created leaf, ``O_NOFOLLOW``.
"""

from __future__ import annotations

import ast
import errno
import importlib
import inspect
import json
import os
import re
import stat
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

from evalproviders_phase4_matrix import status  # type: ignore[import-not-found,unused-ignore]
from evalproviders_phase4_support import (  # type: ignore[import-not-found,unused-ignore]
    CANARY_KEY,
    MR_LINE,
    LlmRig,
    SimRun,
    TtsRig,
    chat_reply,
    client_mod,
    llm_context,
    llm_request,
    secrets_mod,
    simlog_mod,
    tts_context,
    tts_reply,
    tts_request,
    tts_voice,
)
from evalproviders_support import (  # type: ignore[import-not-found,unused-ignore]
    MESSAGE_CANARY,
    SRC,
    TEXT_CANARY,
    ScriptedTransport,
    Sleeps,
    Ticker,
    WallClock,
    error_body,
    json_response,
    ok_body,
    small_plan,
)

STAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$")
REQUEST_KEYS = {
    "at",
    "event",
    "stage",
    "index",
    "attempt",
    "bytes_sent",
    "characters",
    "audio_seconds",
    "status",
    "error_kind",
    "elapsed_ms",
    "request_id",
}
CONNECT_KEYS = {"at", "event", "stage", "dialect"}
END_KEYS = {"at", "event", "stage", "end_reason", "counts"}
PHONE = "+919800000000"
HOSTILE_URL = "https://evil.example/path?token=abc"
DIALECTS = ("pipecat-protobuf/1", "twilio-media-stream/1", "smallwebrtc/1")
END_REASONS = (
    "script_complete",
    "caller_end_call",
    "agent_hangup",
    "max_duration",
    "max_turns",
    "max_silence",
    "kill_switch",
    "setup_failed",
    "transport_error",
    "brain_invalid_output",
    "spend_cap",
    "brain_failed",
    "voice_failed",
)


def _check_line(line: dict[str, Any]) -> None:
    assert STAMP.match(line["at"]), line["at"]
    if line["event"] in {"before", "after"}:
        assert set(line) == REQUEST_KEYS
        assert line["stage"] in {"tts", "llm", "stt"}
        for name in ("index", "attempt", "bytes_sent"):
            assert type(line[name]) is int, name
        assert line["bytes_sent"] > 0
        assert line["characters"] is None or type(line["characters"]) is int
        assert line["audio_seconds"] is None or re.fullmatch(
            r"0|[1-9][0-9]*|[1-9][0-9]*\.[0-9]{0,17}[1-9]|0\.[0-9]{0,17}[1-9]",
            line["audio_seconds"],
        )
        assert line["request_id"] is None or re.fullmatch(
            r"[A-Za-z0-9_.:-]{1,128}", line["request_id"]
        )
        assert line["error_kind"] is None or re.fullmatch(r"[a-z0-9_]{1,64}", line["error_kind"])
        assert line["status"] is None or type(line["status"]) is int
    elif line["event"] == "connect":
        assert set(line) == CONNECT_KEYS
        assert line["stage"] == "call"
        assert line["dialect"] in DIALECTS
    else:
        assert line["event"] == "end"
        assert set(line) == END_KEYS
        assert line["stage"] == "call"
        assert line["end_reason"] in END_REASONS
        assert all(re.fullmatch(r"[a-z][a-z0-9_]{0,63}", k) for k in line["counts"])
        assert all(type(v) is int and v >= 0 for v in line["counts"].values())


# ----------------------------------------------------------------------- the named tests


def test_sim_call_log_has_no_text_no_number_no_key(tmp_path: Path) -> None:
    """A8 / IA4: across a TTS call, an LLM call and an STT call on one shared log, with texts
    that hold a phone number, a URL and canaries, with hostile error bodies, request ids and
    exceptions, the log holds none of them, and every line has exactly the members of its event."""
    evil_id = f"id\n{TEXT_CANARY} with spaces"
    voice = tts_voice(model="bulbul:v3", speaker="tracerspeaker")
    spoken = f"{MR_LINE} {PHONE} {HOSTILE_URL} {TEXT_CANARY}"
    llm_errors = json_response(error_body(message=f"{MESSAGE_CANARY} {PHONE}"), status=500)
    run = SimRun(
        tmp_path,
        tts=[
            tts_reply("tts-1"),
            json_response(
                error_body(
                    "rate_limit_exceeded_error", message=f"{MESSAGE_CANARY} {PHONE}", rid="e-1"
                ),
                status=429,
            ),
            httpx.ReadTimeout(f"timeout {CANARY_KEY}"),
            tts_reply(evil_id),
            json_response(
                error_body(message=MESSAGE_CANARY, rid=f"bad id {CANARY_KEY}"), status=400
            ),
        ],
        llm=[
            chat_reply(f"{spoken} reply", reply_id="chat-1"),
            llm_errors,
            llm_errors,
            llm_errors,
            llm_errors,
        ],
        stt=[json_response(ok_body(f"said {TEXT_CANARY}", request_id="s-1"))],
    )
    for number in range(3):
        run.tts.synthesise(tts_request(number, spoken, voice=voice), tts_context())
    run.llm.complete(
        llm_request(0, messages=[("system", f"call {PHONE} now {HOSTILE_URL}"), ("user", spoken)]),
        llm_context(),
    )
    run.llm.complete(llm_request(1), llm_context())
    run.stt_client.send_chunk(small_plan().chunks[0])
    run.log.connect(dialect="twilio-media-stream/1")
    run.log.end(end_reason="spend_cap", counts={"tts_requests": 3, "llm_requests": 1})
    raw = run.log.path.read_bytes()
    assert raw.isascii()
    text = raw.decode("ascii")
    for canary in (CANARY_KEY, TEXT_CANARY, MESSAGE_CANARY):
        assert canary not in text, canary
    for forbidden in (
        "919800000000",
        "evil.example",
        "http",
        "token",
        "api-subscription",
        "Authorization",
        "bulbul",
        "tracerspeaker",
        "sarvam-105b",
        "with spaces",
        "reply",
        "said",
        str(tmp_path),
    ):
        assert forbidden not in text, forbidden
    lines = [json.loads(x) for x in text.splitlines()]
    assert text.count("\n") == len(lines), "one line each"
    assert text.endswith("\n"), "one line each"
    for line in lines:
        _check_line(line)
    assert {x["stage"] for x in lines if x["event"] == "after"} == {"tts", "llm", "stt"}
    assert [x["event"] for x in lines if x["stage"] == "call"] == ["connect", "end"]
    end = lines[-1]
    assert (end["end_reason"], end["counts"]) == (
        "spend_cap",
        {"tts_requests": 3, "llm_requests": 1},
    )
    tts_after = [x for x in lines if x["event"] == "after" and x["stage"] == "tts"]
    assert [x["request_id"] for x in tts_after][:2] == ["tts-1", "e-1"]
    for x in tts_after:
        assert x["audio_seconds"] is None
        assert type(x["characters"]) is int
    assert tts_after[0]["characters"] == len(spoken), "code points: a count, not the text"


def test_sim_call_log_written_before_and_after_each_request(tmp_path: Path) -> None:
    """A8: while a request is in flight the log already holds its ``before`` line, on disk; every
    attempt, retries included, has both lines; a crash leaves the ``before`` line."""
    for kind in ("tts", "llm"):
        seen: list[list[dict[str, Any]]] = []
        holder: dict[str, Any] = {}

        def observe(
            code: int,
            kind: str = kind,
            seen: list[list[dict[str, Any]]] = seen,
            holder: dict[str, Any] = holder,
        ) -> Any:
            def handler(request: httpx.Request) -> httpx.Response:
                text = holder["rig"].log.path.read_text("ascii")
                seen.append([json.loads(line) for line in text.splitlines()])
                if code == 200:
                    answer: httpx.Response = (
                        tts_reply("in-flight")
                        if kind == "tts"
                        else chat_reply(reply_id="in-flight")
                    )
                    return answer
                response: httpx.Response = status(code)
                return response

            return handler

        steps = [observe(503), httpx.ConnectError("x"), observe(200)]
        rig: Any = (
            TtsRig(tmp_path / kind, steps) if kind == "tts" else LlmRig(tmp_path / kind, steps)
        )
        holder["rig"] = rig
        rig.call(tts_request(7) if kind == "tts" else llm_request(7))
        assert len(seen) == 2
        assert [(x["event"], x["attempt"]) for x in seen[0]] == [("before", 1)]
        assert [(x["event"], x["attempt"]) for x in seen[1]] == [
            ("before", 1),
            ("after", 1),
            ("before", 2),
            ("after", 2),
            ("before", 3),
        ]
        final = rig.log_lines
        assert [(x["event"], x["attempt"], x["stage"], x["index"]) for x in final] == [
            (event, attempt, kind, 7) for attempt in (1, 2, 3) for event in ("before", "after")
        ]
        for line in final:
            if line["event"] == "before":
                assert (line["status"], line["error_kind"], line["elapsed_ms"]) == (
                    None,
                    None,
                    None,
                )
                assert line["request_id"] is None
        afters = [x for x in final if x["event"] == "after"]
        assert [x["status"] for x in afters] == [503, None, 200]
        assert [x["error_kind"] for x in afters] == [None, "connect_error", None]
        assert afters[2]["request_id"] == "in-flight"
        for line, request in zip(
            final, [r for r in rig.transport.requests for _ in (0, 1)], strict=True
        ):
            assert line["bytes_sent"] == len(request.content), "the bytes of the request body"
        if kind == "tts":
            assert {x["characters"] for x in final} == {len(MR_LINE)}
            assert {x["audio_seconds"] for x in final} == {None}
        else:
            assert {x["characters"] for x in final} == {None}
            assert {x["audio_seconds"] for x in final} == {None}


def test_sim_call_log_written_before_and_after_each_request_a_crash_leaves_the_before_line(
    tmp_path: Path,
) -> None:
    """ADR 0024 rule 15: a billable call that never answered still has its trace."""

    class Crash(BaseException):
        pass

    rig = TtsRig(tmp_path, [tts_reply(), Crash()])
    rig.call(tts_request(0))
    with pytest.raises(Crash):
        rig.call(tts_request(1))
    assert [(x["event"], x["index"]) for x in rig.log_lines] == [
        ("before", 0),
        ("after", 0),
        ("before", 1),
    ], "a billable call that never answered still has its trace"


def test_sim_call_log_written_before_and_after_each_request_clocks_are_injected(
    tmp_path: Path,
) -> None:
    """The elapsed time and the stamps come from the injected clocks, so tests predict them."""
    rig = TtsRig(tmp_path, [status(503), httpx.ReadTimeout("x"), tts_reply()])
    rig.call()
    lines = rig.log_lines
    assert [x["elapsed_ms"] for x in lines if x["event"] == "after"] == [250, 250, 250]
    assert rig.ticker.calls == 6, "the monotonic clock is read twice per attempt"
    assert [x["at"] for x in lines] == [f"2026-10-25T09:00:0{i}.123456Z" for i in range(1, 7)]


def test_sim_call_log_written_before_and_after_each_request_refusals_and_hits_leave_no_line(
    tmp_path: Path,
) -> None:
    """A refusal and a cache hit are not requests: no line."""
    from evalproviders_phase4_support import LONG_MR, cache_mod, core_tts  # type: ignore[import-not-found,unused-ignore]

    cache = cache_mod().TtsCache.open(tmp_path / "out")
    rig = TtsRig(tmp_path, [tts_reply()], cache=cache)
    rig.call(tts_request(0))
    assert len(rig.log_lines) == 2
    rig.call(tts_request(1))  # a cache hit: no request, so no line
    with pytest.raises(core_tts().TtsTextTooLongError):
        rig.call(tts_request(2, LONG_MR + "x"))
    assert len(rig.log_lines) == 2
    assert len(rig.transport.requests) == 1


def test_sim_call_log_the_stt_engine_writes_into_the_same_file(tmp_path: Path) -> None:
    """A8: "two lines per attempt of every engine": the STT client takes the CallLog-shaped view
    ``SimCallLog.for_stt()`` and its lines carry the stage ``stt`` and the chunk index."""
    log = simlog_mod().SimCallLog.open(tmp_path / "out", clock=WallClock())
    transport = ScriptedTransport([json_response(ok_body(request_id="s-1"))])
    client = client_mod().SarvamClient(
        secrets_mod().ApiKey(CANARY_KEY),
        call_log=log.for_stt(),
        transport=transport,
        sleep=Sleeps(),
        monotonic=Ticker(),
    )
    chunk = small_plan().chunks[1]
    client.send_chunk(chunk)
    client.close()
    log.close()
    lines = [json.loads(x) for x in log.path.read_text("ascii").splitlines()]
    assert [(x["event"], x["stage"], x["index"], x["attempt"]) for x in lines] == [
        ("before", "stt", 1, 1),
        ("after", "stt", 1, 1),
    ]
    for line in lines:
        _check_line(line)
        assert line["characters"] is None
        assert line["bytes_sent"] == len(transport.requests[0].content)
    assert lines[1]["request_id"] == "s-1"
    assert re.fullmatch(r"[0-9.]+", lines[0]["audio_seconds"])


def test_sim_call_log_the_stt_view_has_the_signatures_of_the_call_log(tmp_path: Path) -> None:
    """The STT client takes the view in place of a ``CallLog``; closing the view leaves the
    shared log open."""
    log = simlog_mod().SimCallLog.open(tmp_path / "out")
    view = log.for_stt()
    calllog = importlib.import_module("tracer_evalproviders.calllog").CallLog
    for name in ("before", "after"):
        mine = inspect.signature(getattr(view, name)).parameters
        theirs = inspect.signature(getattr(calllog, name)).parameters
        assert set(mine) == set(theirs) - {"self"}, name
        assert all(p.kind is inspect.Parameter.KEYWORD_ONLY for p in mine.values()), name
    view.close()
    log.before(stage="llm", index=0, attempt=1, bytes_sent=5)
    log.close()
    assert len(log.path.read_text("ascii").splitlines()) == 1, "closing a view leaves the log open"


# ---------------------------------------------------------------- connect and end lines


def test_sim_call_log_connect_and_end_lines(tmp_path: Path) -> None:
    """A8: one ``connect`` line per dialect and one ``end`` line per end reason, with counts."""
    log = simlog_mod().SimCallLog.open(tmp_path / "out", clock=WallClock())
    for dialect in DIALECTS:
        log.connect(dialect=dialect)
    for reason in END_REASONS:
        log.end(end_reason=reason, counts={})
    log.end(end_reason="script_complete", counts={"tts_requests": 2, "frames_sent": 1500})
    log.close()
    lines = [json.loads(x) for x in log.path.read_text("ascii").splitlines()]
    assert len(lines) == len(DIALECTS) + len(END_REASONS) + 1
    for line in lines:
        _check_line(line)
    assert [x["dialect"] for x in lines[:3]] == list(DIALECTS)
    assert lines[-1]["counts"] == {"tts_requests": 2, "frames_sent": 1500}


@pytest.mark.parametrize(
    "bad",
    [
        {"dialect": "other/1"},
        {"dialect": "twilio-media-stream/2"},
        {"dialect": "wss://host/ws"},
        {"dialect": PHONE},
        {"dialect": ""},
        {"dialect": None},
    ],
)
def test_sim_call_log_connect_refuses_what_is_not_a_dialect_id(
    tmp_path: Path, bad: dict[str, Any]
) -> None:
    """A8: the connect line holds a dialect id and nothing else (no URL, no number)."""
    log = simlog_mod().SimCallLog.open(tmp_path / "out")
    with pytest.raises((ValueError, TypeError)):
        log.connect(**bad)
    log.close()
    assert log.path.read_text("ascii") == ""


@pytest.mark.parametrize(
    ("reason", "counts"),
    [
        ("finished", {}),
        ("", {}),
        (None, {}),
        ("script_complete", {PHONE: 1}),
        ("script_complete", {"a b": 1}),
        ("script_complete", {"Upper": 1}),
        ("script_complete", {"1x": 1}),
        ("script_complete", {"k" * 65: 1}),
        ("script_complete", {"n": True}),
        ("script_complete", {"n": 1.5}),
        ("script_complete", {"n": "5"}),
        ("script_complete", {"n": -1}),
        ("script_complete", {"n": None}),
        ("script_complete", [("n", 1)]),
    ],
)
def test_sim_call_log_end_refuses_an_unknown_reason_and_any_count_that_is_not_a_plain_integer(
    tmp_path: Path, reason: Any, counts: Any
) -> None:
    """A8: the end line holds a closed end reason and machine-keyed integers, so a number or a
    text cannot ride in it."""
    log = simlog_mod().SimCallLog.open(tmp_path / "out")
    with pytest.raises((ValueError, TypeError)):
        log.end(end_reason=reason, counts=counts)
    log.close()
    assert log.path.read_text("ascii") == ""


@pytest.mark.parametrize(
    "fields",
    [
        {"stage": "call", "index": 0, "attempt": 1, "bytes_sent": 1},
        {"stage": "other", "index": 0, "attempt": 1, "bytes_sent": 1},
        {"stage": "tts", "index": 0, "attempt": 1, "bytes_sent": 1},
        {
            "stage": "tts",
            "index": 0,
            "attempt": 1,
            "bytes_sent": 1,
            "characters": 5,
            "audio_seconds": "1",
        },
        {"stage": "tts", "index": 0, "attempt": 1, "bytes_sent": 1, "characters": MR_LINE},
        {"stage": "tts", "index": 0, "attempt": 1, "bytes_sent": 1, "characters": True},
        {"stage": "stt", "index": 0, "attempt": 1, "bytes_sent": 1},
        {"stage": "stt", "index": 0, "attempt": 1, "bytes_sent": 1, "audio_seconds": "abc"},
        {"stage": "stt", "index": 0, "attempt": 1, "bytes_sent": 1, "audio_seconds": 1.5},
        {
            "stage": "stt",
            "index": 0,
            "attempt": 1,
            "bytes_sent": 1,
            "audio_seconds": "1",
            "characters": 1,
        },
        {"stage": "llm", "index": 0, "attempt": 1, "bytes_sent": 1, "characters": 5},
        {"stage": "llm", "index": 0, "attempt": 1, "bytes_sent": 1, "audio_seconds": "1"},
        {"stage": "llm", "index": 0, "attempt": 1, "bytes_sent": "5"},
        {"stage": "llm", "index": 0.0, "attempt": 1, "bytes_sent": 5},
        {"stage": "llm", "index": 0, "attempt": True, "bytes_sent": 5},
    ],
)
def test_sim_call_log_a_before_line_holds_integers_and_the_one_size_its_stage_has(
    tmp_path: Path, fields: dict[str, Any]
) -> None:
    """tts counts characters, stt counts audio seconds, llm counts neither: a line cannot carry
    text in a field made for a number, and it is refused before it is written."""
    log = simlog_mod().SimCallLog.open(tmp_path / "out")
    with pytest.raises((ValueError, TypeError)):
        log.before(**fields)
    log.close()
    assert log.path.read_text("ascii") == ""


def test_sim_call_log_only_ever_names_a_safe_request_id_and_error_kind(tmp_path: Path) -> None:
    """ADR 0024 rule 15: only a plain identifier is logged as an id, only a short machine key as
    an error kind."""
    log = simlog_mod().SimCallLog.open(tmp_path / "out")
    ids = ["ok-1", "a b", "x\ny", "\u00e9", "", "x" * 129, None]
    kinds = ["read_timeout", "a b", "Read_Timeout", "x\ny", "k" * 65, "", None]
    for number, (rid, kind) in enumerate(zip(ids, kinds, strict=True)):
        log.after(
            stage="llm",
            index=number,
            attempt=1,
            bytes_sent=1,
            status=200,
            error_kind=kind,
            elapsed_ms=1,
            request_id=rid,
        )
    log.close()
    lines = [json.loads(x) for x in log.path.read_text("ascii").splitlines()]
    assert [x["request_id"] for x in lines] == ["ok-1", None, None, None, None, None, None]
    assert [x["error_kind"] for x in lines] == ["read_timeout", None, None, None, None, None, None]


# ------------------------------------------------------------------------- the file rules


def test_sim_call_log_is_private_and_append_only(tmp_path: Path) -> None:
    """ADR 0024 rule 15: mode 0600, directory 0700 whatever the umask, append-only, closing twice
    is harmless."""
    out = tmp_path / "nested" / "out"
    previous = os.umask(0o000)
    try:
        log = simlog_mod().SimCallLog.open(out)
    finally:
        os.umask(previous)
    assert simlog_mod().SIM_CALL_LOG_NAME == "sim_call_log.jsonl"
    assert log.path == out / "sim_call_log.jsonl"
    assert stat.S_IMODE(out.stat().st_mode) == 0o700, oct(out.stat().st_mode)
    assert stat.S_IMODE(log.path.stat().st_mode) == 0o600
    log.before(stage="llm", index=0, attempt=1, bytes_sent=10)
    log.close()
    log.close()  # closing twice is harmless
    again = simlog_mod().SimCallLog.open(out)
    again.before(stage="llm", index=1, attempt=1, bytes_sent=11)
    again.close()
    lines = [json.loads(x) for x in log.path.read_text("ascii").splitlines()]
    assert [x["index"] for x in lines] == [0, 1], "an existing log is appended to, never truncated"


def test_sim_call_log_an_existing_directory_keeps_its_mode_and_a_loose_log_is_tightened(
    tmp_path: Path,
) -> None:
    """ADR 0024 rule 15: the user's directory is not changed; a looser log is tightened to 0600."""
    out = tmp_path / "out"
    out.mkdir()
    out.chmod(0o755)
    existing = out / "sim_call_log.jsonl"
    existing.write_text("", "ascii")
    existing.chmod(0o666)
    log = simlog_mod().SimCallLog.open(out)
    log.close()
    assert stat.S_IMODE(out.stat().st_mode) == 0o755, "the user's directory is not changed"
    assert stat.S_IMODE(existing.stat().st_mode) == 0o600


def test_sim_call_log_a_symbolic_link_in_place_of_the_log_is_refused(tmp_path: Path) -> None:
    """ADR 0024 rule 15 (batch G F7): ``O_NOFOLLOW``; nothing is written through a link."""
    out = tmp_path / "out"
    out.mkdir()
    target = tmp_path / "elsewhere.txt"
    target.write_text("keep me", "ascii")
    target.chmod(0o644)
    (out / "sim_call_log.jsonl").symlink_to(target)
    with pytest.raises(OSError) as raised:  # noqa: PT011 (the text is the platform's)
        simlog_mod().SimCallLog.open(out)
    assert raised.value.errno in (errno.ELOOP, errno.EMLINK, errno.EPERM)
    assert target.read_text("ascii") == "keep me"
    assert stat.S_IMODE(target.stat().st_mode) == 0o644


def test_sim_call_log_a_dangling_link_is_refused_and_creates_nothing(tmp_path: Path) -> None:
    """A dangling link is refused and its target is not created."""
    out = tmp_path / "out"
    out.mkdir()
    target = tmp_path / "not-yet.txt"
    (out / "sim_call_log.jsonl").symlink_to(target)
    with pytest.raises(OSError):  # noqa: PT011
        simlog_mod().SimCallLog.open(out)
    assert not target.exists()


def test_sim_call_log_the_constructor_refuses_a_link_too(tmp_path: Path) -> None:
    """The constructor and ``open`` refuse a link alike."""
    target = tmp_path / "t.txt"
    target.write_text("x", "ascii")
    link = tmp_path / "link.jsonl"
    link.symlink_to(target)
    with pytest.raises(OSError):  # noqa: PT011
        simlog_mod().SimCallLog(link)
    assert target.read_text("ascii") == "x"


def test_sim_call_log_module_opens_with_no_follow_and_imports_only_the_standard_library() -> None:
    """The log module is stdlib plus the package's own safe-id helper and opens with no-follow."""
    text = (SRC / "simcalllog.py").read_text("utf-8")
    assert "O_NOFOLLOW" in text
    tops = set()
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.Import):
            tops |= {a.name.split(".")[0] for a in node.names}
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            tops.add(node.module.split(".")[0])
    stdlib = set(sys.stdlib_module_names)
    assert tops - stdlib <= {"tracer_evalcore", "tracer_evalproviders"}, tops - stdlib


def test_sim_call_log_elapsed_is_the_floor_of_the_monotonic_difference_in_milliseconds(
    tmp_path: Path,
) -> None:
    """The elapsed time is the injected monotonic difference times 1000, floored."""
    rig = TtsRig(tmp_path, [tts_reply(), status(503), tts_reply()])
    rig.ticker.step = 3.5
    rig.call(tts_request(0))
    rig.call(tts_request(1))
    assert [x["elapsed_ms"] for x in rig.log_lines if x["event"] == "after"] == [3500, 3500, 3500]


def test_sim_call_log_stamps_are_utc_whatever_the_clock_zone(tmp_path: Path) -> None:
    """ADR 0024 rule 15: ``at`` is a UTC instant; a clock in another zone is converted."""
    from datetime import datetime, timedelta, timezone

    zone = timezone(timedelta(hours=5, minutes=30))
    log = simlog_mod().SimCallLog.open(
        tmp_path / "out", clock=lambda: datetime(2026, 10, 25, 14, 30, 5, 123456, tzinfo=zone)
    )
    log.connect(dialect="smallwebrtc/1")
    log.close()
    (line,) = [json.loads(x) for x in log.path.read_text("ascii").splitlines()]
    assert line["at"] == "2026-10-25T09:00:05.123456Z"


def test_sim_call_log_every_line_is_synced_to_disk_before_the_call_returns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR 0024 rule 15: the ``before`` line is "flushed before sending": each line is written
    and ``os.fsync``ed before the logging call returns, so a crash mid-request leaves it."""
    synced: list[int] = []
    real = os.fsync

    def counting(descriptor: int) -> None:
        synced.append(descriptor)
        real(descriptor)

    monkeypatch.setattr(os, "fsync", counting)
    log = simlog_mod().SimCallLog.open(tmp_path / "out")
    log.before(stage="llm", index=0, attempt=1, bytes_sent=5)
    assert len(synced) == 1
    log.after(
        stage="llm",
        index=0,
        attempt=1,
        bytes_sent=5,
        status=200,
        error_kind=None,
        elapsed_ms=1,
        request_id="r",
    )
    log.connect(dialect="smallwebrtc/1")
    log.end(end_reason="script_complete", counts={})
    assert len(synced) == 4
    log.close()
