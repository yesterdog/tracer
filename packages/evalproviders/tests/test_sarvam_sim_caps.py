# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""The spend caps at the adapters (ADR 0026 rule 15; contract docs/phase4/api-surface-batch-b.md
section 9). Not a SCOPE name of P4-08 to P4-12: the rule says "before each request the adapter
checks the cap against what was already used", and ``TtsCapReachedError`` / ``LlmCapReachedError``
exist in the engine boundary since P4-03, so the behaviour is pinned here and not left to P4-23.

TTS characters are known exactly before the send: a request that would cross the cap is not sent.
The LLM cap counts the tokens the provider reports: a request is sent only while the reported
total plus that request's ``max_tokens`` stays within the cap. A cap is an integer or ``None``
(no cap, the fake engines); it has no default in code.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

import pytest

from evalproviders_phase4_matrix import status  # type: ignore[import-not-found,unused-ignore]
from evalproviders_phase4_support import (  # type: ignore[import-not-found,unused-ignore]
    LlmRig,
    TtsRig,
    chat_mod,
    chat_reply,
    core_llm,
    core_tts,
    llm_request,
    tts_mod,
    tts_reply,
    tts_request,
)


def _refused(rig: Any, outcome: Any, cls: Any, index: int, reason: str) -> None:
    assert type(outcome.failure) is cls
    assert (outcome.failure.index, outcome.failure.reason) == (index, reason)
    assert outcome.failure.status is None
    assert outcome.usage == ()
    assert outcome.failure.__cause__ is None
    assert outcome.failure.__context__ is None


def test_sarvam_tts_characters_cap_refuses_the_request_that_would_cross_it(tmp_path: Path) -> None:
    """ADR 0026 rule 15: a request that would cross the characters cap is not sent; a refusal
    uses nothing."""
    rig = TtsRig(tmp_path, [tts_reply("a"), tts_reply("b")], cap=10)
    assert rig.call(tts_request(0, "a" * 6)).failure is None
    before = (len(rig.transport.requests), len(rig.log_lines))
    refused = rig.call(tts_request(1, "b" * 5))  # 6 + 5 = 11 > 10
    _refused(rig, refused, core_tts().TtsCapReachedError, 1, "tts_chars")
    assert (len(rig.transport.requests), len(rig.log_lines)) == before, "not sent, not logged"
    # a refused request uses nothing: 6 + 4 = 10 is within the cap
    assert rig.call(tts_request(2, "c" * 4)).failure is None
    exhausted = rig.call(tts_request(3, "d"))
    _refused(rig, exhausted, core_tts().TtsCapReachedError, 3, "tts_chars")


def test_sarvam_tts_characters_are_counted_in_code_points(tmp_path: Path) -> None:
    """The cap counts code points, like the 2,500 limit and ``input_characters``."""
    rig = TtsRig(tmp_path, [tts_reply()], cap=3)
    assert rig.call(tts_request(0, "\U0001f600\u0915\u094d")).failure is None  # 3 code points
    refused = rig.call(tts_request(1, "x"))
    _refused(rig, refused, core_tts().TtsCapReachedError, 1, "tts_chars")


def test_sarvam_tts_a_request_that_was_sent_counts_even_when_it_failed(tmp_path: Path) -> None:
    """Whether a failed attempt is billed is unverified (ADR 0024 rule 9): the cap counts the
    characters of a request once, from its first send, and never counts them again for a retry."""
    rig = TtsRig(tmp_path, [status(403), status(503), tts_reply("b")], cap=10)
    assert rig.call(tts_request(0, "a" * 6)).failure is not None  # sent, refused by the server
    refused = rig.call(tts_request(1, "b" * 5))
    _refused(rig, refused, core_tts().TtsCapReachedError, 1, "tts_chars")
    retried = rig.call(tts_request(2, "c" * 4))  # 6 + 4 = 10; its retry is not charged twice
    assert retried.failure is None
    assert len(rig.transport.requests) == 3


def test_sarvam_tts_cap_none_means_no_cap_and_zero_refuses_everything(tmp_path: Path) -> None:
    """No cap is ``None`` (fake engines); a cap of 0 refuses every request."""
    rig = TtsRig(tmp_path, [tts_reply(f"r{i}") for i in range(12)], cap=None)
    for index in range(12):
        assert rig.call(tts_request(index, "x" * 2000)).failure is None
    zero = TtsRig(tmp_path / "zero", [tts_reply()], cap=0)
    _refused(zero, zero.call(tts_request(0, "x")), core_tts().TtsCapReachedError, 0, "tts_chars")
    assert zero.transport.requests == []


@pytest.mark.parametrize("bad", [-1, True, 1.5, "10", 10**30])
def test_sarvam_tts_a_cap_that_is_not_a_plain_non_negative_integer_is_refused(
    tmp_path: Path, bad: Any
) -> None:
    """A cap is an integer from 0 to 2**53 - 1 or ``None``."""
    with pytest.raises((ValueError, TypeError)):
        TtsRig(tmp_path, [], cap=bad)


def test_sarvam_the_caps_have_no_default_in_code() -> None:
    """ADR 0026 rule 15: no default cap in code, in the engines or in their factories."""
    for cls, name in (
        (tts_mod().SarvamTts, "cap_characters"),
        (chat_mod().SarvamLlm, "cap_tokens"),
    ):
        parameter = inspect.signature(cls.__init__).parameters[name]
        assert parameter.default is inspect.Parameter.empty, name
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY, name
    for factory, name in (
        (tts_mod().create_sarvam_tts, "cap_characters"),
        (chat_mod().create_sarvam_llm, "cap_tokens"),
    ):
        assert inspect.signature(factory).parameters[name].default is inspect.Parameter.empty


def test_sarvam_llm_token_cap_counts_the_reported_tokens_plus_the_next_max_tokens(
    tmp_path: Path,
) -> None:
    """ADR 0026 rule 15: reported total plus the next request's ``max_tokens`` must stay within
    the cap."""
    rig = LlmRig(
        tmp_path,
        [
            chat_reply(reply_id="a", prompt=30, completion=20),
            chat_reply(reply_id="b", prompt=30, completion=30),
        ],
        cap=100,
    )
    assert rig.call(llm_request(0, max_tokens=40)).failure is None  # 0 + 40 <= 100; reports 50
    assert (
        rig.call(llm_request(1, max_tokens=40)).failure is None
    )  # 50 + 40 = 90 <= 100; reports 60
    before = (len(rig.transport.requests), len(rig.log_lines))
    refused = rig.call(llm_request(2, max_tokens=40))  # 110 + 40 > 100
    _refused(rig, refused, core_llm().LlmCapReachedError, 2, "llm_tokens")
    assert (len(rig.transport.requests), len(rig.log_lines)) == before


def test_sarvam_llm_token_cap_a_request_that_ends_exactly_on_the_cap_is_sent(
    tmp_path: Path,
) -> None:
    """The cap is inclusive: a total equal to the cap is within it."""
    rig = LlmRig(
        tmp_path,
        [chat_reply(reply_id="a", prompt=10, completion=10), chat_reply(reply_id="b")],
        cap=100,
    )
    assert rig.call(llm_request(0, max_tokens=100)).failure is None  # 0 + 100 == 100; reports 20
    assert rig.call(llm_request(1, max_tokens=80)).failure is None  # 20 + 80 == 100; reports 16
    refused = rig.call(llm_request(2, max_tokens=85))  # 36 + 85 > 100
    _refused(rig, refused, core_llm().LlmCapReachedError, 2, "llm_tokens")


def test_sarvam_llm_token_cap_a_failed_request_reports_nothing_and_cached_tokens_are_not_added(
    tmp_path: Path,
) -> None:
    """A failed request reports no tokens; cached tokens are part of the prompt tokens and are
    not added twice."""
    rig = LlmRig(
        tmp_path,
        [status(403), chat_reply(prompt=30, completion=20, cached=10), chat_reply()],
        cap=100,
    )
    assert rig.call(llm_request(0, max_tokens=50)).failure is not None  # nothing reported
    assert rig.call(llm_request(1, max_tokens=50)).failure is None  # 0 + 50 <= 100; reports 50
    # prompt tokens already include the cached ones: 30 + 20, not 30 + 10 + 20
    assert rig.call(llm_request(2, max_tokens=50)).failure is None  # 50 + 50 == 100
    assert len(rig.transport.requests) == 3


def test_sarvam_llm_token_cap_none_means_no_cap(tmp_path: Path) -> None:
    """No cap is ``None``."""
    rig = LlmRig(tmp_path, [chat_reply(reply_id=f"r{i}", prompt=10**6) for i in range(5)], cap=None)
    for index in range(5):
        assert rig.call(llm_request(index, max_tokens=10**9)).failure is None


@pytest.mark.parametrize("bad", [-1, True, 1.5, "10", 2**60])
def test_sarvam_llm_a_cap_that_is_not_a_plain_non_negative_integer_is_refused(
    tmp_path: Path, bad: Any
) -> None:
    """A cap is an integer from 0 to 2**53 - 1 or ``None``."""
    with pytest.raises((ValueError, TypeError)):
        LlmRig(tmp_path, [], cap=bad)
