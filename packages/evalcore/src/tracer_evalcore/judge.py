# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""The judge engine boundary and the driver of one golden item (ADR 0030 rules 7, 16, 17, 18).

The shape of the language-model boundary (``tracer_evalcore.llm``): a fully explicit request, a
typed outcome that holds a reply text or a failure, facts-only errors raised outside handlers, and
usage handed out before the next request. ``judge_item`` asks the judge once, validates the reply
(never repairing it), sends the identical request once more after an invalid reply, and returns
what was decided as an ``JudgeItemResult``: a verdict, ``cannot_tell``, or an error with a code. The
text of an invalid reply is counted and never kept.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Final, Protocol, runtime_checkable

from tracer_evalcore._engine_errors import FactError
from tracer_evalcore.golden import MATCH_MODES, GoldenItem
from tracer_evalcore.judgeoutput import JudgeOutputInvalid, parse_judge_output
from tracer_evalcore.judgeprompt import build_judge_messages, prompt_sha256
from tracer_evalcore.llm import LlmMessage
from tracer_evalcore.stt import UsageSink
from tracer_evalschema.ids import RunId
from tracer_evalschema.usage import UsageRecord

if TYPE_CHECKING:
    from tracer_evalcore.judgewindow import AnswerWindow

__all__ = [
    "EXTRA_HEADER_NAMES",
    "JUDGE_ERROR_CODES",
    "JudgeAuthError",
    "JudgeCapReachedError",
    "JudgeConfigurationError",
    "JudgeContext",
    "JudgeEngine",
    "JudgeError",
    "JudgeItemResult",
    "JudgeKeyMissingError",
    "JudgeOutcome",
    "JudgeParams",
    "JudgeRateLimited",
    "JudgeRequest",
    "JudgeRequestRejected",
    "JudgeResponseInvalid",
    "JudgeServerError",
    "JudgeTransportError",
    "UsageSink",
    "UsageStreamingJudgeEngine",
    "judge_item",
    "judge_once",
]

JUDGE_ERROR_CODES: Final = (
    "judge_undetermined",
    "judge_output_invalid",
    "judge_request_failed",
    "judge_auth_failed",
    "judge_requests_cap_reached",
    "judge_tokens_cap_reached",
    "question_not_sent",
    "answer_window_empty",
)
EXTRA_HEADER_NAMES: Final = ("anthropic-workspace-id", "HTTP-Referer", "X-OpenRouter-Title")
_MODEL: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,255}")
_EFFORT: Final = re.compile(r"[a-z][a-z0-9_]{0,31}")
_SLUG: Final = re.compile(r"[a-z0-9][a-z0-9._/-]{0,63}")
_ITEM_ID: Final = re.compile(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*")
_HEADER_VALUE: Final = re.compile(r"[\x20-\x7e]{1,200}")
_SHA: Final = re.compile(r"[0-9a-f]{64}")
_MAX_SAFE: Final = 2**53 - 1


class JudgeError(FactError):
    """Base of every judge failure."""


class JudgeAuthError(JudgeError):
    """HTTP 401 or 403, or the provider's invalid-key code. Never retried."""


class JudgeRequestRejected(JudgeError):
    """Any other 4xx except 429. Never retried."""


class JudgeRateLimited(JudgeError):
    """429 after the last attempt."""


class JudgeServerError(JudgeError):
    """5xx after the last attempt."""


class JudgeTransportError(JudgeError):
    """A connect, read or write error or a timeout after the last attempt."""


class JudgeResponseInvalid(JudgeError):
    """A response that is not the documented success shape. Never retried."""


class JudgeCapReachedError(JudgeError):
    """The request would cross a cap of the run; it was not sent."""


class JudgeConfigurationError(JudgeError, ValueError):
    """The run is configured so that no request may be sent."""


class JudgeKeyMissingError(JudgeConfigurationError):
    """The API key is missing, empty or unusable."""


@dataclass(frozen=True)
class JudgeParams:
    """The call parameters of the configuration; no field has a default."""

    model: str
    max_tokens: int
    temperature: int | float | None
    seed: int | None
    reasoning_effort: str | None
    provider_slug: str | None
    extra_headers: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        if type(self.model) is not str or not _MODEL.fullmatch(self.model):
            raise ValueError("model: not an accepted value")
        if type(self.max_tokens) is not int or not 1 <= self.max_tokens <= _MAX_SAFE:
            raise ValueError("max_tokens: outside 1 to 2**53 - 1")
        temperature = self.temperature
        if temperature is not None and (
            type(temperature) not in (int, float) or not 0 <= temperature <= 2
        ):
            raise ValueError("temperature: not a number from 0 to 2 or None")
        if self.seed is not None and (
            type(self.seed) is not int or not 0 <= self.seed <= _MAX_SAFE
        ):
            raise ValueError("seed: not a non-negative integer or None")
        if self.reasoning_effort is not None and (
            type(self.reasoning_effort) is not str or not _EFFORT.fullmatch(self.reasoning_effort)
        ):
            raise ValueError("reasoning_effort: not an accepted value or None")
        if self.provider_slug is not None and (
            type(self.provider_slug) is not str or not _SLUG.fullmatch(self.provider_slug)
        ):
            raise ValueError("provider_slug: not an accepted value or None")
        if type(self.extra_headers) is not tuple:
            raise ValueError("extra_headers: not a tuple of pairs")
        for pair in self.extra_headers:
            if (
                type(pair) is not tuple
                or len(pair) != 2
                or pair[0] not in EXTRA_HEADER_NAMES
                or type(pair[1]) is not str
                or not _HEADER_VALUE.fullmatch(pair[1])
            ):
                raise ValueError("extra_headers: not an accepted (name, value) pair")


@dataclass(frozen=True)
class JudgeContext:
    run_id: RunId
    created_at: datetime


@dataclass(frozen=True)
class JudgeRequest:
    """One request. ``item_id``, ``attempt``, ``match_mode`` and ``fact_count`` are for the engines
    that name an exchange or answer from the request; none of them is sent to a provider."""

    index: int
    item_id: str
    attempt: int
    match_mode: str
    fact_count: int
    messages: tuple[LlmMessage, ...]
    params: JudgeParams

    def __post_init__(self) -> None:
        if type(self.index) is not int or self.index < 0:
            raise ValueError("index: not a non-negative integer")
        if type(self.item_id) is not str or not _ITEM_ID.fullmatch(self.item_id):
            raise ValueError("item_id: not an accepted value")
        if type(self.attempt) is not int or self.attempt not in (1, 2):
            raise ValueError("attempt: not 1 or 2")
        if self.match_mode not in MATCH_MODES:
            raise ValueError("match_mode: not an accepted value")
        if type(self.fact_count) is not int or self.fact_count < 0:
            raise ValueError("fact_count: not a non-negative integer")
        if (self.match_mode == "all_facts") != (self.fact_count >= 1):
            raise ValueError("fact_count: at least 1 for all_facts and 0 for equivalent_answer")
        if type(self.messages) is not tuple or len(self.messages) != 2:
            raise ValueError("messages: not a tuple of two messages")
        if not all(isinstance(m, LlmMessage) for m in self.messages):
            raise TypeError("messages: not LlmMessage records")
        if not isinstance(self.params, JudgeParams):
            raise TypeError("params: not JudgeParams")


@dataclass(frozen=True)
class JudgeOutcome:
    """A reply text or a failure, never both; ``usage`` holds one record per answered request."""

    text: str | None
    finish_reason: str | None
    reported_model: str | None
    request_id: str | None
    exchange_sha256: str | None
    params_sent: Mapping[str, Any] | None
    usage: tuple[UsageRecord, ...]
    failure: JudgeError | None

    def __post_init__(self) -> None:
        if (self.text is None) == (self.failure is None):
            raise ValueError("an outcome holds a text or a failure, exactly one")
        if self.exchange_sha256 is not None and not _SHA.fullmatch(self.exchange_sha256):
            raise ValueError("exchange_sha256: not 64 lower-case hex digits")
        for record in self.usage:
            if not isinstance(record, UsageRecord):
                raise TypeError("usage: not a UsageRecord")
            if record.stage != "judge" or record.domain != "evaluation":
                raise ValueError("usage: not a judge record of the evaluation domain")


@runtime_checkable
class JudgeEngine(Protocol):
    """One request in, a typed outcome out."""

    def complete(self, request: JudgeRequest, context: JudgeContext) -> JudgeOutcome: ...


@runtime_checkable
class UsageStreamingJudgeEngine(Protocol):
    """An engine that hands out the usage of each answered request before it returns."""

    streams_usage: bool

    def complete(
        self, request: JudgeRequest, context: JudgeContext, *, on_usage: UsageSink | None = None
    ) -> JudgeOutcome: ...


def judge_once(
    engine: JudgeEngine | UsageStreamingJudgeEngine,
    request: JudgeRequest,
    context: JudgeContext,
    *,
    on_usage: UsageSink,
) -> JudgeOutcome:
    """Send one request; its usage reaches ``on_usage`` before this function returns."""
    if isinstance(engine, UsageStreamingJudgeEngine) and engine.streams_usage is True:
        return engine.complete(request, context, on_usage=on_usage)
    outcome = engine.complete(request, context)
    for record in outcome.usage:
        on_usage(record)
    return outcome


@dataclass(frozen=True)
class JudgeItemResult:
    """What was decided for one golden item; see ``judge_item``."""

    item_id: str
    state: str
    verdict: str | None
    reason_code: str | None
    facts: tuple[tuple[int, str], ...]
    error_code: str | None
    window: AnswerWindow
    attempts: int
    regenerated: bool
    prompt_sha256: str | None
    exchange_sha256: str | None
    reported_model: str | None
    params: Mapping[str, Any]
    usage: tuple[UsageRecord, ...]
    failure: JudgeError | None

    def __post_init__(self) -> None:
        if self.state not in ("verdict", "cannot_tell", "error"):
            raise ValueError("state: not an accepted value")
        if self.error_code is not None and self.error_code not in JUDGE_ERROR_CODES:
            raise ValueError("error_code: not an accepted value")
        if self.state == "verdict" and (
            self.verdict not in ("agrees", "disagrees") or self.error_code is not None
        ):
            raise ValueError("a verdict state holds agrees or disagrees and no error")
        if self.state == "cannot_tell" and (
            self.verdict != "cannot_tell" or self.error_code != "judge_undetermined"
        ):
            raise ValueError("a cannot_tell state holds that verdict and judge_undetermined")
        if self.state == "error" and (
            self.verdict is not None
            or self.reason_code is not None
            or self.facts
            or self.error_code in (None, "judge_undetermined")
        ):
            raise ValueError("an error state holds an error code and no verdict")
        if self.attempts not in (0, 1, 2) or self.regenerated != (self.attempts == 2):
            raise ValueError("attempts and regenerated disagree")

    @property
    def stops_run(self) -> bool:
        """An authentication failure or a cap ends the judging of the run (rules 16 and 17)."""
        return isinstance(self.failure, JudgeAuthError | JudgeCapReachedError)


def _error_code(failure: JudgeError) -> str:
    if isinstance(failure, JudgeAuthError):
        return "judge_auth_failed"
    if isinstance(failure, JudgeCapReachedError):
        if failure.reason == "judge_requests":
            return "judge_requests_cap_reached"
        if failure.reason == "judge_tokens":
            return "judge_tokens_cap_reached"
    return "judge_request_failed"


def judge_item(
    *,
    engine: JudgeEngine | UsageStreamingJudgeEngine,
    item: GoldenItem,
    window: AnswerWindow,
    params: JudgeParams,
    context: JudgeContext,
    first_index: int,
    on_usage: UsageSink,
) -> JudgeItemResult:
    """Judge one item over its answer window; at most two requests (the second is the identical
    request, sent after an invalid reply). Never raises for a reply or a failure of a request."""
    if window.status != "ok":
        return JudgeItemResult(
            item_id=item.id,
            state="error",
            verdict=None,
            reason_code=None,
            facts=(),
            error_code=window.status,
            window=window,
            attempts=0,
            regenerated=False,
            prompt_sha256=None,
            exchange_sha256=None,
            reported_model=None,
            params={},
            usage=(),
            failure=None,
        )
    messages = build_judge_messages(item, window.text)
    digest = prompt_sha256(messages)
    used: list[UsageRecord] = []
    last: JudgeOutcome
    for attempt in (1, 2):
        request = JudgeRequest(
            index=first_index + attempt - 1,
            item_id=item.id,
            attempt=attempt,
            match_mode=item.match_mode,
            fact_count=len(item.expected_facts),
            messages=messages,
            params=params,
        )
        outcome = judge_once(engine, request, context, on_usage=on_usage)
        used.extend(outcome.usage)
        last = outcome
        if outcome.failure is not None:
            failure = outcome.failure
            return _judgement(
                item, window, "error", attempt, digest, outcome, used, error=_error_code(failure)
            )
        verdict = None
        if outcome.finish_reason != "length":
            try:
                verdict = parse_judge_output(
                    outcome.text or "",
                    match_mode=item.match_mode,
                    fact_count=len(item.expected_facts),
                )
            except JudgeOutputInvalid:
                verdict = None
        if verdict is not None:
            state = "cannot_tell" if verdict.verdict == "cannot_tell" else "verdict"
            return _judgement(
                item,
                window,
                state,
                attempt,
                digest,
                outcome,
                used,
                verdict=verdict.verdict,
                reason=verdict.reason_code,
                facts=verdict.facts,
                error="judge_undetermined" if state == "cannot_tell" else None,
            )
    return _judgement(item, window, "error", 2, digest, last, used, error="judge_output_invalid")


def _judgement(
    item: GoldenItem,
    window: AnswerWindow,
    state: str,
    attempts: int,
    digest: str,
    outcome: JudgeOutcome,
    used: Sequence[UsageRecord],
    *,
    verdict: str | None = None,
    reason: str | None = None,
    facts: tuple[tuple[int, str], ...] = (),
    error: str | None = None,
) -> JudgeItemResult:
    return JudgeItemResult(
        item_id=item.id,
        state=state,
        verdict=verdict,
        reason_code=reason,
        facts=facts,
        error_code=error,
        window=window,
        attempts=attempts,
        regenerated=attempts == 2,
        prompt_sha256=digest,
        exchange_sha256=outcome.exchange_sha256,
        reported_model=outcome.reported_model,
        params=dict(outcome.params_sent or {}),
        usage=tuple(used),
        failure=outcome.failure,
    )
