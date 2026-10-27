# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Hostile replies and a failing call log at the TTS and chat adapters (contract
docs/phase4/api-surface-batch-b.md sections 2.4, 3.3 and 6.4; ADR 0024 rules 8, 9 and 15, ADR
0026 rule 22 for the idea that nothing a provider sends may break the run).

A 200 reply that is hostile (nesting, bytes that are not UTF-8, a lone surrogate, numbers that
are not integers) is a typed ``ResponseInvalid`` outcome and never an exception out of the
engine; a call log that cannot be written stops the run before the next request, and a reply
whose ``after`` line could not be written is dropped unread (not counted, not cached).
"""

from __future__ import annotations

import errno
from pathlib import Path
from typing import Any

import pytest

from evalproviders_phase4_support import (  # type: ignore[import-not-found,unused-ignore]
    LlmRig,
    SimRun,
    TtsRig,
    cache_mod,
    chat_mod,
    chat_reply,
    core_llm,
    core_tts,
    llm_context,
    llm_request,
    secrets_mod,
    tts_context,
    tts_mod,
    tts_reply,
    tts_request,
)
from evalproviders_support import (  # type: ignore[import-not-found,unused-ignore]
    CANARY_KEY,
    ScriptedTransport,
    Sleeps,
    Ticker,
    json_response,
)

DEEP = b"[" * 200_000 + b"]" * 200_000
TTS_SHELL = '{"request_id": "%s", "audios": ["AAEC"]%s}'
CHAT_SHELL = (
    '{"id": "%s", "choices": [{"message": {"content": "%s"}}], '
    '"usage": {"prompt_tokens": %s, "completion_tokens": 1}%s}'
)

TTS_HOSTILE: list[tuple[str, bytes]] = [
    ("deep nesting as the body", DEEP),
    (
        "deep nesting in an unknown member",
        (TTS_SHELL % ("r", ', "x": ' + "[" * 100_000 + "]" * 100_000)).encode(),
    ),
    ("invalid UTF-8", b'{"request_id": "\xff\xfe", "audios": ["AAEC"]}'),
    ("lone surrogate in the id", (TTS_SHELL % ("\\ud800", "")).encode()),
    ("lone low surrogate in the id", (TTS_SHELL % ("\\udc00x", "")).encode()),
    ("a number beyond any float", (TTS_SHELL % ("r", ', "x": 1e999999999')).encode()),
    ("a 5,000 digit integer", (TTS_SHELL % ("r", ', "x": ' + "9" * 5000)).encode()),
    ("NUL bytes", b"\x00" * 100),
]
CHAT_HOSTILE: list[tuple[str, bytes]] = [
    ("deep nesting as the body", DEEP),
    (
        "deep nesting in an unknown member",
        (CHAT_SHELL % ("i", "c", "1", ', "x": ' + "[" * 100_000 + "]" * 100_000)).encode(),
    ),
    ("invalid UTF-8", b'{"id": "\xff\xfe", "choices": []}'),
    ("lone surrogate in the id", (CHAT_SHELL % ("\\ud800", "c", "1", "")).encode()),
    ("lone surrogate in the content", (CHAT_SHELL % ("i", "\\udfff", "1", "")).encode()),
    ("a token count of 1e999", (CHAT_SHELL % ("i", "c", "1e999", "")).encode()),
    ("a token count of 1E2", (CHAT_SHELL % ("i", "c", "1E2", "")).encode()),
    ("a 5,000 digit token count", (CHAT_SHELL % ("i", "c", "9" * 5000, "")).encode()),
    ("NUL bytes", b"\x00" * 100),
]


@pytest.mark.parametrize(("label", "body"), TTS_HOSTILE, ids=[b[0] for b in TTS_HOSTILE])
def test_sarvam_tts_a_hostile_200_reply_is_a_typed_failure_never_an_exception(
    tmp_path: Path, label: str, body: bytes
) -> None:
    """A hostile 200 reply (nesting, bytes, surrogates, huge numbers) is a typed outcome, never
    an exception out of the engine."""
    rig = TtsRig(tmp_path, [json_response(body)])
    outcome = rig.call()
    assert type(outcome.failure) is core_tts().TtsResponseInvalid, label
    assert outcome.usage == ()
    assert outcome.audio is None
    assert outcome.failure.status == 200
    assert len(rig.transport.requests) == 1
    assert rig.sleeps.waits == []
    assert outcome.failure.__context__ is None
    assert outcome.failure.__cause__ is None


@pytest.mark.parametrize(("label", "body"), CHAT_HOSTILE, ids=[b[0] for b in CHAT_HOSTILE])
def test_sarvam_llm_a_hostile_200_reply_is_a_typed_failure_never_an_exception(
    tmp_path: Path, label: str, body: bytes
) -> None:
    """A hostile 200 reply (nesting, bytes, surrogates, huge numbers) is a typed outcome, never
    an exception out of the engine."""
    rig = LlmRig(tmp_path, [json_response(body)])
    outcome = rig.call()
    assert type(outcome.failure) is core_llm().LlmResponseInvalid, label
    assert outcome.usage == ()
    assert outcome.text is None
    assert outcome.failure.status == 200
    assert len(rig.transport.requests) == 1
    assert outcome.failure.__context__ is None
    assert outcome.failure.__cause__ is None


@pytest.mark.parametrize(
    "reason_body",
    [
        ("not_json", DEEP),
        ("not_json", (TTS_SHELL % ("\\ud800", "")).encode()),
    ],
)
def test_sarvam_tts_hostile_reasons_are_the_short_keys_of_the_contract(
    tmp_path: Path, reason_body: tuple[str, bytes]
) -> None:
    """The reason keys of the hostile cases are the contract's."""
    reason, body = reason_body
    rig = TtsRig(tmp_path, [json_response(body)])
    assert rig.call().failure.reason == reason


def test_sarvam_llm_a_lone_surrogate_in_the_content_is_not_a_text(tmp_path: Path) -> None:
    """A lone surrogate cannot be encoded: it is refused at the parse, not crashed on later."""
    rig = LlmRig(tmp_path, [json_response((CHAT_SHELL % ("i", "\\ud800", "1", "")).encode())])
    assert rig.call().failure.reason == "not_json"


# ----------------------------------------------------------------- one request in flight


def test_the_engines_send_one_request_at_a_time(tmp_path: Path) -> None:
    """ADR 0024 rule 2: calls are sequential, one request in flight."""
    run = SimRun(
        tmp_path,
        tts=[tts_reply(f"t{i}") for i in range(3)],
        llm=[chat_reply(reply_id=f"c{i}") for i in range(3)],
    )
    for index in range(3):
        run.tts.synthesise(tts_request(index), tts_context())
        run.llm.complete(llm_request(index), llm_context())
    assert run.tts_transport.max_active == 1
    assert run.llm_transport.max_active == 1


# ------------------------------------------------------------- a call log that cannot write


class FailingLog:
    """A call log whose k-th write fails with ``ENOSPC``; it keeps what it was given."""

    def __init__(self, fail_at: int) -> None:
        self.fail_at = fail_at
        self.calls: list[tuple[str, int]] = []

    def _tick(self, event: str, index: int) -> None:
        self.calls.append((event, index))
        if len(self.calls) == self.fail_at:
            raise OSError(errno.ENOSPC, "no space")

    def before(self, **fields: Any) -> None:
        self._tick("before", fields["index"])

    def after(self, **fields: Any) -> None:
        self._tick("after", fields["index"])

    def close(self) -> None:
        return None


def _engine(kind: str, log: Any, transport: ScriptedTransport, cache: Any = None) -> Any:
    key = secrets_mod().ApiKey(CANARY_KEY)
    common: dict[str, Any] = {
        "call_log": log,
        "transport": transport,
        "sleep": Sleeps(),
        "monotonic": Ticker(),
    }
    if kind == "tts":
        return tts_mod().SarvamTts(key, cap_characters=None, cache=cache, **common)
    return chat_mod().SarvamLlm(key, cap_tokens=None, **common)


def _call(kind: str, engine: Any, index: int, sink: Any) -> Any:
    if kind == "tts":
        return engine.synthesise(tts_request(index), tts_context(), on_usage=sink)
    return engine.complete(llm_request(index), llm_context(), on_usage=sink)


@pytest.mark.parametrize("kind", ["tts", "llm"])
def test_a_call_log_that_fails_before_a_request_stops_it_unsent(tmp_path: Path, kind: str) -> None:
    """ADR 0024 rule 15: the ``before`` line is written before the send, so a log that cannot be
    written means the request is not sent; the usage of the earlier request is with the sink."""
    transport = ScriptedTransport(
        [tts_reply("a") if kind == "tts" else chat_reply(reply_id="a")] * 2
    )
    sink: list[Any] = []
    engine = _engine(kind, FailingLog(fail_at=3), transport)
    first = _call(kind, engine, 0, sink.append)
    assert first.failure is None
    with pytest.raises(OSError, match="no space"):
        _call(kind, engine, 1, sink.append)
    assert len(transport.requests) == 1, "the second request was never sent"
    assert len(sink) == 1
    engine.close()


@pytest.mark.parametrize("kind", ["tts", "llm"])
def test_a_reply_whose_after_line_cannot_be_written_is_dropped_unread(
    tmp_path: Path, kind: str
) -> None:
    """The record and the cache entry exist only for a reply that is in the log: a failing
    ``after`` line propagates, and nothing was handed out or cached."""
    cache = cache_mod().TtsCache.open(tmp_path / "out") if kind == "tts" else None
    transport = ScriptedTransport([tts_reply("a") if kind == "tts" else chat_reply(reply_id="a")])
    sink: list[Any] = []
    engine = _engine(kind, FailingLog(fail_at=2), transport, cache)
    with pytest.raises(OSError, match="no space"):
        _call(kind, engine, 0, sink.append)
    assert sink == []
    if cache is not None:
        assert list((tmp_path / "out" / "tts-cache").iterdir()) == []
    engine.close()


@pytest.mark.parametrize("kind", ["tts", "llm"])
def test_the_engines_are_context_managers_and_close_twice(tmp_path: Path, kind: str) -> None:
    """The engines close their HTTP client, once or twice, and leave the shared log alone."""
    run = SimRun(tmp_path)
    engine = run.tts if kind == "tts" else run.llm
    with engine as entered:
        assert entered is engine
    engine.close()
    engine.close()
