# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Exchange files, the replay engine and the fake engine of the judge (ADR 0030 rules 19 and 25).

Each answered judge request leaves ``<out>/judge/<run>/item-<id>-<attempt>.json`` (the document
``tracer.judge_exchange/1``: the request messages, the raw reply body, the request id and the
status), mode 0600 in a directory of mode 0700, opened without following a link and never over an
existing file. The store of the run holds only the SHA-256 of the file, never a raw reply. The
``replay:<dir>`` engine answers from those files and sends nothing; the ``fake:<scenario>`` engine
is a closed set of deterministic scenarios that answer from the request alone.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import stat
import warnings
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final

from tracer_evalcore.errors import InputRefusedError
from tracer_evalcore.judge import (
    JudgeAuthError,
    JudgeConfigurationError,
    JudgeContext,
    JudgeOutcome,
    JudgeParams,
    JudgeRequest,
    JudgeResponseInvalid,
)
from tracer_evalcore.judgerecords import validate_judge_exchange_document
from tracer_evalproviders import judgeprofiles
from tracer_evalproviders.openai_compat import ChatReply, parse_chat_response
from tracer_evalschema.digest import jcs_bytes
from tracer_evalschema.errors import DepthLimitExceeded, DuplicateKeyError
from tracer_evalschema.loader import loads_strict

__all__ = [
    "EXCHANGE_DIR_NAME",
    "FAKE_SCENARIOS",
    "MAX_EXCHANGE_BYTES",
    "ExchangeStore",
    "FakeJudge",
    "JudgeExchangeError",
    "JudgeExchangeModeWarning",
    "ReplayJudge",
    "create_fake_judge",
    "create_replay_judge",
    "exchange_directory",
    "exchange_file_name",
    "planned_fake_params",
]

EXCHANGE_DIR_NAME: Final = "judge"
MAX_EXCHANGE_BYTES: Final = 262_144
FAKE_SCENARIOS: Final = (
    "agrees",
    "disagrees",
    "cannot_tell",
    "invalid_once",
    "invalid",
    "fenced",
    "auth_failure",
)
_ITEM_ID: Final = re.compile(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*")


class JudgeExchangeModeWarning(UserWarning):
    """The ``judge`` directory under the output directory was open to other users and was made
    private. The text names no path."""


class JudgeExchangeError(InputRefusedError):
    """An exchange file or directory was refused. ``code`` is the reason and the message."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def exchange_directory(out_dir: str | os.PathLike[str], run_id: object) -> Path:
    """``<out>/judge/<run id>``."""
    return Path(out_dir) / EXCHANGE_DIR_NAME / str(run_id)


def exchange_file_name(item_id: object, attempt: object) -> str:
    """``item-<id>-<attempt>.json``; an id that could leave the directory is a ``ValueError``."""
    if type(item_id) is not str or len(item_id) > 40 or _ITEM_ID.fullmatch(item_id) is None:
        raise ValueError("item_id: not an item identifier")
    if type(attempt) is not int or attempt not in (1, 2):
        raise ValueError("attempt: not 1 or 2")
    return f"item-{item_id}-{attempt}.json"


def _private_directory(path: Path, *, tighten: bool = False) -> None:
    """Create ``path`` (and any missing parent) with mode 0700, or accept an existing real
    directory that no other user can use. With ``tighten`` (the level this module itself creates,
    ``<out>/judge``) an existing directory open to others is made 0700 and a warning is issued; a
    link or a file is always refused. The user's own output directory is never changed."""
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        if not path.parent.exists():
            _private_directory(path.parent)
        with contextlib.suppress(FileExistsError):
            os.mkdir(path, 0o700)
        os.chmod(path, 0o700)
        info = os.lstat(path)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise JudgeExchangeError("exchange_directory_not_private")
    if info.st_mode & 0o077:
        if not tighten:
            raise JudgeExchangeError("exchange_directory_not_private")
        os.chmod(path, 0o700)
        warnings.warn(
            "the judge directory was open to other users and is now private",
            JudgeExchangeModeWarning,
            stacklevel=3,
        )


def _read_all(descriptor: int, limit: int) -> bytes:
    """Up to ``limit`` bytes of an open file; ``os.read`` may return fewer than are there."""
    chunks: list[bytes] = []
    remaining = limit
    while remaining > 0:
        chunk = os.read(descriptor, remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _read_file(directory: Path, name: str) -> tuple[dict[str, Any], str]:
    """``(document, SHA-256 of the file's bytes)`` of one exchange file, read under the rules of
    rule 19: a regular file, no link, no group or other bit, a bounded size, a valid document."""
    path = directory / name
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        raise JudgeExchangeError("exchange_missing") from None
    except OSError:
        raise JudgeExchangeError("exchange_unreadable") from None
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise JudgeExchangeError("exchange_not_a_regular_file")
    if info.st_mode & 0o077:
        raise JudgeExchangeError("exchange_mode_too_open")
    if info.st_size > MAX_EXCHANGE_BYTES:
        raise JudgeExchangeError("exchange_invalid")
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            opened = os.fstat(descriptor)
            if (opened.st_ino, opened.st_dev) != (info.st_ino, info.st_dev):
                raise JudgeExchangeError("exchange_not_a_regular_file")
            data = _read_all(descriptor, MAX_EXCHANGE_BYTES + 1)
        finally:
            os.close(descriptor)
    except OSError:
        raise JudgeExchangeError("exchange_unreadable") from None
    try:
        document = loads_strict(data.decode("utf-8"))
        if not isinstance(document, dict):
            raise ValueError("not an object")
        validate_judge_exchange_document(document)
    except (ValueError, DepthLimitExceeded, DuplicateKeyError, RecursionError):
        raise JudgeExchangeError("exchange_invalid") from None
    return document, hashlib.sha256(data).hexdigest()


class ExchangeStore:
    """The exchange files of one run."""

    def __init__(self, directory: str | os.PathLike[str]) -> None:
        self.directory = Path(directory)
        parent = self.directory.parent
        if parent.name == EXCHANGE_DIR_NAME and os.path.lexists(parent):
            _private_directory(parent, tighten=True)
        _private_directory(self.directory)

    def write(self, item_id: str, attempt: int, document: Mapping[str, Any]) -> str:
        """Write the document; the SHA-256 (hex) of the bytes written."""
        name = exchange_file_name(item_id, attempt)
        validate_judge_exchange_document(document)
        data = jcs_bytes(document)
        try:
            descriptor = os.open(
                self.directory / name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
            )
        except FileExistsError:
            raise JudgeExchangeError("exchange_exists") from None
        except OSError:
            raise JudgeExchangeError("exchange_unwritable") from None
        try:
            os.fchmod(descriptor, 0o600)
            view = memoryview(data)
            while view:
                view = view[os.write(descriptor, view) :]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return hashlib.sha256(data).hexdigest()

    def read(self, item_id: str, attempt: int) -> dict[str, Any]:
        document, _ = _read_file(self.directory, exchange_file_name(item_id, attempt))
        return document


# ------------------------------------------------------------------------------------- replay


def _failure(error: JudgeResponseInvalid) -> JudgeOutcome:
    return JudgeOutcome(
        text=None,
        finish_reason=None,
        reported_model=None,
        request_id=None,
        exchange_sha256=None,
        params_sent=None,
        usage=(),
        failure=error,
    )


class ReplayJudge:
    """Answers from the stored exchanges; it cannot send (no transport, no key, no URL)."""

    def __init__(self, directory: str | os.PathLike[str], profile: judgeprofiles.JudgeProfile):
        self._directory = Path(directory)
        try:
            info = os.lstat(self._directory)
        except OSError:
            raise JudgeExchangeError("exchange_missing") from None
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise JudgeExchangeError("exchange_directory_not_private")
        self._profile = profile

    def complete(self, request: JudgeRequest, context: JudgeContext) -> JudgeOutcome:
        name = exchange_file_name(request.item_id, request.attempt)
        try:
            document, digest = _read_file(self._directory, name)
        except JudgeExchangeError as error:
            reason = "replay_missing" if error.code == "exchange_missing" else "replay_unreadable"
            return _failure(
                JudgeResponseInvalid(reason=reason, index=request.index, attempts=request.attempt)
            )
        stored = [(m["role"], m["content"]) for m in document["messages"]]
        asked = [(m.role, m.content) for m in request.messages]
        if stored != asked:
            return _failure(
                JudgeResponseInvalid(
                    reason="replay_request_differs", index=request.index, attempts=request.attempt
                )
            )
        if document["status"] != 200:
            return _failure(
                JudgeResponseInvalid(
                    reason="replay_status",
                    status=200,
                    index=request.index,
                    attempts=request.attempt,
                )
            )
        parsed = parse_chat_response(
            document["response_body"].encode("utf-8"), index=request.index, attempts=request.attempt
        )
        if not isinstance(parsed, ChatReply):
            return _failure(parsed)
        return JudgeOutcome(
            text=parsed.content,
            finish_reason=parsed.finish_reason,
            reported_model=parsed.model,
            request_id=document["request_id"],
            exchange_sha256=digest,
            params_sent=judgeprofiles.params_as_sent(self._profile, request),
            usage=(),
            failure=None,
        )


def create_replay_judge(
    directory: str | os.PathLike[str], *, profile: judgeprofiles.JudgeProfile
) -> ReplayJudge:
    return ReplayJudge(directory, profile)


# --------------------------------------------------------------------------------------- fake


def _reply(
    request: JudgeRequest, verdict: str, all_facts: str, equivalent: str, status: str
) -> str:
    facts = []
    if request.match_mode == "all_facts":
        facts = [{"index": i, "status": status} for i in range(request.fact_count)]
    code = all_facts if request.match_mode == "all_facts" else equivalent
    return json.dumps(
        {"verdict": verdict, "reason_code": code, "facts": facts}, separators=(",", ":")
    )


class FakeJudge:
    """A deterministic judge: the same request gives the same outcome; nothing is sent."""

    def __init__(self, scenario: str) -> None:
        self._scenario = scenario

    def complete(self, request: JudgeRequest, context: JudgeContext) -> JudgeOutcome:
        scenario = self._scenario
        if scenario == "auth_failure":
            error = JudgeAuthError(index=request.index, status=401, attempts=1)
            return JudgeOutcome(
                text=None,
                finish_reason=None,
                reported_model=None,
                request_id=None,
                exchange_sha256=None,
                params_sent=None,
                usage=(),
                failure=error,
            )
        agrees = _reply(request, "agrees", "all_facts_stated", "meaning_same", "stated")
        if scenario == "agrees":
            text = agrees
        elif scenario == "disagrees":
            text = _reply(request, "disagrees", "fact_absent", "meaning_differs", "absent")
        elif scenario == "cannot_tell":
            text = _reply(
                request,
                "cannot_tell",
                "insufficient_information",
                "insufficient_information",
                "cannot_tell",
            )
        elif scenario == "invalid_once":
            text = agrees if request.attempt == 2 else "I cannot answer in the requested form."
        elif scenario == "fenced":
            text = "```json\n" + agrees + "\n```"
        else:
            text = "I cannot answer in the requested form."
        sent = planned_fake_params(request.params)
        return JudgeOutcome(
            text=text,
            finish_reason="stop",
            reported_model=f"fake/{scenario}",
            request_id=f"fake-{request.index}",
            exchange_sha256=None,
            params_sent=sent,
            usage=(),
            failure=None,
        )


def planned_fake_params(params: JudgeParams) -> dict[str, Any]:
    """What the fake engine records as sent (the ``params`` of the document of a fake run)."""
    sent: dict[str, Any] = {"max_tokens": params.max_tokens}
    if params.temperature is not None:
        sent["temperature"] = params.temperature
    return sent


def create_fake_judge(scenario: str) -> FakeJudge:
    if type(scenario) is not str or scenario not in FAKE_SCENARIOS:
        raise JudgeConfigurationError(reason="unknown_scenario")
    return FakeJudge(scenario)
