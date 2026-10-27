# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""The three caller brains (ADR 0028 rule 1): scripted, LLM and replay.

Each is a ``CallerBrain`` (``tracer_evalcore.brain``) whose only effects are a line to be
synthesised and sent, or the end of the call. Scripted makes no request at all. Replay reads
``turn-NNNN.json`` files once, when it is built, so that a missing file is a refusal before any
key is read or any socket is opened. The LLM brain sends fully explicit requests through an
``LlmEngine``, validates every reply (``tracer_evalcore.callerline``) and regenerates at most
once. Pure: no clock, no randomness, no environment, no logging, no network.
"""

from __future__ import annotations

import json
import os
import re
import stat
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Final, NoReturn

from tracer_evalcore.brain import BrainDecision
from tracer_evalcore.callerline import (
    ALLOWED_LANGUAGES,
    check_caller_reply,
    require_max_line_chars,
)
from tracer_evalcore.errors import EvalcoreError
from tracer_evalcore.llm import (
    LlmCapReachedError,
    LlmContext,
    LlmMessage,
    LlmRequest,
    complete_once,
)
from tracer_evalcore.prompt import PromptInputs, build_messages, template_sha256
from tracer_evalcore.simtypes import END_SENTINEL, MAX_LINE_CHARS
from tracer_evalcore.stt import UsageSink
from tracer_evalschema.evaluator import ScriptedTurn

__all__ = [
    "REPLAY_FILE_MAX_BYTES",
    "REPLAY_FILE_NAME",
    "LlmBrain",
    "ReplayBrain",
    "ReplayDirectoryError",
    "ScriptedBrain",
]

REPLAY_FILE_NAME: Final = "turn-{:04d}.json"
REPLAY_FILE_MAX_BYTES: Final = 65536
_MAX_SAFE: Final = 2**53 - 1  # I-JSON, ADR 0027
_NAME: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,63}")
_REPLAY_CODES: Final = (
    "directory_missing",
    "missing",
    "not_a_regular_file",
    "too_large",
    "unreadable",
    "not_json",
    "bad_shape",
)


def _require_index(turn_index: object) -> int:
    if type(turn_index) is not int or turn_index < 0:
        raise ValueError("turn_index: not a non-negative integer")
    return turn_index


def _require_positive(value: object, name: str) -> int:
    if type(value) is not int or not 1 <= value <= _MAX_SAFE:
        raise ValueError(f"{name}: not an integer from 1 to 2**53 - 1")
    return value


def _require_language(language: object) -> str:
    if type(language) is not str or language not in ALLOWED_LANGUAGES:
        raise ValueError("language: no allowed character set")
    return language


# ------------------------------------------------------------------------------- scripted


class ScriptedBrain:
    """The evaluator's ``scripted_turns``: the line of turn N is the entry of index N. No model."""

    mode = "scripted"

    def __init__(self, scripted_turns: Sequence[ScriptedTurn]) -> None:
        if not isinstance(scripted_turns, Sequence) or isinstance(scripted_turns, str | bytes):
            raise TypeError("scripted_turns: not a sequence")
        turns = list(scripted_turns)
        if not all(isinstance(turn, ScriptedTurn) for turn in turns):
            raise TypeError("scripted_turns: not ScriptedTurn records")
        if [turn.index for turn in turns] != list(range(len(turns))):
            raise ValueError("scripted_turns: indexes are not 0, 1, 2, ... in order")
        if any(len(turn.text) > MAX_LINE_CHARS for turn in turns):
            raise ValueError("scripted_turns: a line is longer than the limit")
        self._lines = tuple(turn.text for turn in turns)

    def next_line(self, turn_index: int, agent_text: str | None) -> BrainDecision:
        index = _require_index(turn_index)
        if index >= len(self._lines):
            return BrainDecision(None, "script_complete", 0, 0)
        return BrainDecision(self._lines[index], None, 0, 0)

    def profile_block(self) -> dict[str, Any]:
        return {"mode": self.mode}


# --------------------------------------------------------------------------------- replay


class ReplayDirectoryError(EvalcoreError, ValueError):
    """A replay directory that cannot serve the call; holds the turn and a code, never a path or
    a line."""

    def __init__(self, *, turn: int | None, code: str) -> None:
        if turn is not None and (type(turn) is not int or turn < 1):
            raise ValueError("turn: not a positive integer")
        if code not in _REPLAY_CODES:
            raise ValueError("code: not a replay code")
        self.turn = turn
        self.code = code
        where = "replay directory" if turn is None else REPLAY_FILE_NAME.format(turn)
        super().__init__(f"ReplayDirectoryError: {where}: {code}")


class _DuplicateKey(Exception):
    """A JSON object with the same key twice."""


def _reject_constant(name: str) -> NoReturn:
    raise ValueError("constant")


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    keys = [key for key, _ in pairs]
    if len(set(keys)) != len(keys):
        raise _DuplicateKey
    return dict(pairs)


def _read_file(path: Path) -> bytes | str:
    """The bytes of a regular file, or the code that refuses it."""
    code: str | None = None
    data = b""
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return "missing"
    except OSError:
        return "unreadable"
    if not stat.S_ISREG(info.st_mode):
        return "not_a_regular_file"
    if info.st_size > REPLAY_FILE_MAX_BYTES:
        return "too_large"
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        return "unreadable"
    try:
        data = os.read(descriptor, REPLAY_FILE_MAX_BYTES + 1)
    except OSError:
        code = "unreadable"
    finally:
        os.close(descriptor)
    if code is not None:
        return code
    if len(data) > REPLAY_FILE_MAX_BYTES:
        return "too_large"
    return data


def _classify(data: bytes) -> tuple[str | None, str]:
    failure = ""
    document: Any = None
    try:
        document = json.loads(
            data.decode("utf-8"),
            parse_constant=_reject_constant,
            object_pairs_hook=_no_duplicates,
        )
    except _DuplicateKey:
        failure = "bad_shape"
    except (ValueError, RecursionError):
        failure = "not_json"
    if failure:
        return None, failure
    if type(document) is not dict or set(document) != {"line"} or type(document["line"]) is not str:
        return None, "bad_shape"
    line: str = document["line"]
    return line, ""


class ReplayBrain:
    """``--caller fake:<dir>``: the line of turn N is the ``line`` of ``turn-NNNN.json``.

    The files from ``turn-0001.json`` up to ``max_turns``, or up to the first file that holds the
    end sentinel, are checked to exist and parse, and are read, when the brain is built.
    """

    mode = "replay"

    def __init__(
        self, directory: Path | str, *, language: str, max_turns: int, max_line_chars: int
    ) -> None:
        if not isinstance(directory, Path | str):
            raise TypeError("directory: not a path")
        self._language = _require_language(language)
        self._max_turns = _require_positive(max_turns, "max_turns")
        self._max_line_chars = require_max_line_chars(max_line_chars)
        root = Path(directory)
        if not root.is_dir():
            raise ReplayDirectoryError(turn=None, code="directory_missing")
        lines: list[str] = []
        for turn in range(1, self._max_turns + 1):
            got = _read_file(root / REPLAY_FILE_NAME.format(turn))
            if isinstance(got, str):
                raise ReplayDirectoryError(turn=turn, code=got)
            line, failure = _classify(got)
            if line is None:
                raise ReplayDirectoryError(turn=turn, code=failure)
            lines.append(line)
            if line.strip() == END_SENTINEL:
                break
        self._lines = tuple(lines)

    def next_line(self, turn_index: int, agent_text: str | None) -> BrainDecision:
        index = _require_index(turn_index)
        if index >= self._max_turns:
            return BrainDecision(None, "max_turns", 0, 0)
        if index >= len(self._lines):
            raise ValueError("turn_index: beyond the replayed call")
        verdict = check_caller_reply(
            self._lines[index], language=self._language, max_line_chars=self._max_line_chars
        )
        if verdict.kind == "end_call":
            return BrainDecision(None, "caller_end_call", 0, 0)
        if verdict.kind == "line":
            return BrainDecision(verdict.line, None, 0, 0)
        return BrainDecision(None, "brain_invalid_output", 1, 0)

    def profile_block(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "max_line_chars": self._max_line_chars,
            "max_turns": self._max_turns,
            "end_sentinel": END_SENTINEL,
        }


# ------------------------------------------------------------------------------------ llm


class LlmBrain:
    """The LLM caller: one explicit request per turn, one regeneration at most."""

    mode = "llm"

    def __init__(
        self,
        *,
        engine: Any,
        context: LlmContext,
        on_usage: UsageSink,
        provider: str,
        model: str,
        max_tokens: int,
        seed: int | None,
        reasoning_effort: str | None,
        max_line_chars: int,
        max_turns: int,
        template_id: str,
        agent_text_max_chars: int,
        inputs: PromptInputs,
    ) -> None:
        if not callable(getattr(engine, "complete", None)):
            raise TypeError("engine: has no complete method")
        if not isinstance(context, LlmContext):
            raise TypeError("context: not an LlmContext")
        if not callable(on_usage):
            raise TypeError("on_usage: not callable")
        if type(provider) is not str or not _NAME.fullmatch(provider):
            raise ValueError("provider: not an accepted value")
        # the request record validates the model, the token limit, the seed and the effort
        LlmRequest(
            index=0,
            messages=(LlmMessage(role="system", content="x"),),
            model=model,
            max_tokens=max_tokens,
            seed=seed,
            reasoning_effort=reasoning_effort,
        )
        template_sha256(template_id)
        if not isinstance(inputs, PromptInputs):
            raise TypeError("inputs: not PromptInputs")
        _require_language(inputs.language)
        self._engine = engine
        self._context = context
        self._on_usage = on_usage
        self._provider = provider
        self._model = model
        self._max_tokens = max_tokens
        self._seed = seed
        self._effort = reasoning_effort
        self._max_line_chars = require_max_line_chars(max_line_chars)
        self._max_turns = _require_positive(max_turns, "max_turns")
        self._template_id = template_id
        self._agent_cap = _require_positive(agent_text_max_chars, "agent_text_max_chars")
        self._inputs = inputs
        self._conversation: list[tuple[str, str]] = []
        self._turn = 0
        self._requests = 0
        self._ended = False

    def _request(self, messages: tuple[LlmMessage, ...]) -> LlmRequest:
        request = LlmRequest(
            index=self._requests,
            messages=messages,
            model=self._model,
            max_tokens=self._max_tokens,
            seed=self._seed,
            reasoning_effort=self._effort,
        )
        self._requests += 1
        return request

    def next_line(self, turn_index: int, agent_text: str | None) -> BrainDecision:
        if self._ended or type(turn_index) is not int or turn_index != self._turn:
            raise ValueError("turn_index: not the next turn of a live brain")
        if agent_text is not None and type(agent_text) is not str:
            raise TypeError("agent_text: not text or None")
        if turn_index >= self._max_turns:
            self._ended = True
            return BrainDecision(None, "max_turns", 0, 0)
        conversation = list(self._conversation)
        if agent_text is not None:
            conversation.append(("agent", agent_text))
        messages = build_messages(
            template_id=self._template_id,
            inputs=self._inputs,
            conversation=conversation,
            agent_text_max_chars=self._agent_cap,
        )
        invalid = 0
        regenerations = 0
        for attempt in (0, 1):
            outcome = complete_once(
                self._engine,
                self._request(messages),
                self._context,
                on_usage=self._on_usage,
            )
            if outcome.failure is not None:
                self._ended = True
                if isinstance(outcome.failure, LlmCapReachedError):
                    return BrainDecision(None, "spend_cap", invalid, regenerations)
                raise outcome.failure
            if attempt == 1:
                regenerations = 1
            verdict = check_caller_reply(
                str(outcome.text),
                language=self._inputs.language,
                max_line_chars=self._max_line_chars,
            )
            if verdict.kind == "end_call":
                self._ended = True
                return BrainDecision(None, "caller_end_call", invalid, regenerations)
            if verdict.kind == "line":
                self._conversation = [*conversation, ("caller", str(verdict.line))]
                self._turn += 1
                return BrainDecision(verdict.line, None, invalid, regenerations)
            invalid += 1
        self._ended = True
        return BrainDecision(None, "brain_invalid_output", invalid, regenerations)

    def profile_block(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "provider": self._provider,
            "model": self._model,
            "max_tokens": self._max_tokens,
            "seed": self._seed,
            "reasoning_effort": self._effort,
            "max_line_chars": self._max_line_chars,
            "max_turns": self._max_turns,
            "max_regenerations": 1,
            "end_sentinel": END_SENTINEL,
            "prompt": {
                "template_id": self._template_id,
                "template_sha256": template_sha256(self._template_id),
                "agent_text_max_chars": self._agent_cap,
            },
        }
