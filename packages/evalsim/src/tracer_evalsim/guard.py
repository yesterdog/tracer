# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""The files that guard a call: the kill switch and the call lock (ADR 0026 rules 17 and 18).

Both live in ``--out``, which the operator chose; Tracer writes nothing outside it. The kill switch
is a file named ``kill-switch``: its existence, of any kind, means stop; the tool never removes it.
The call lock is ``call.lock``: one line that records the UTC and the monotonic instants of the last
connect, so that a call earlier than ``min_call_interval_ms`` after it is refused, and an exclusive
``flock`` on it for the whole invocation, so that two invocations on one output directory are never
in a call at the same time. Both are opened without following a link. Every refusal is a
``GuardError`` whose message is its code: never a path, never an instant.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import TracebackType
from typing import Final

from tracer_evalsim.errors import EvalsimError

__all__ = [
    "CALL_LOCK_FILE_NAME",
    "CALL_LOCK_FORMAT",
    "GUARD_CODES",
    "KILL_SWITCH_FILE_NAME",
    "MAX_CALL_LOCK_BYTES",
    "CallLock",
    "GuardError",
    "check_call_interval",
    "kill_switch_present",
]

KILL_SWITCH_FILE_NAME: Final = "kill-switch"
CALL_LOCK_FILE_NAME: Final = "call.lock"
CALL_LOCK_FORMAT: Final = "tracer.call_lock/1"
MAX_CALL_LOCK_BYTES: Final = 4096
GUARD_CODES: Final = (
    "call_in_progress",
    "call_lock_clock_inconsistent",
    "call_lock_invalid",
    "kill_switch_file_present",
    "min_call_interval_not_elapsed",
)
_CODE: Final = re.compile(r"[a-z][a-z0-9_]{0,63}")
_UTC: Final = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z")
_MAX_MONOTONIC: Final = 2**63 - 1
_NOFOLLOW: Final = getattr(os, "O_NOFOLLOW", 0)
_CLOEXEC: Final = getattr(os, "O_CLOEXEC", 0)


class GuardError(EvalsimError, ValueError):
    """A guard refused the run. ``code`` is the reason and the message."""

    def __init__(self, code: str) -> None:
        if not isinstance(code, str) or _CODE.fullmatch(code) is None:
            raise ValueError("code is not a machine code")
        self.code = code
        super().__init__(code)


def kill_switch_present(out_dir: str | os.PathLike[str]) -> bool:
    """True if anything is named ``kill-switch`` in ``out_dir`` (a dangling link counts).

    A missing entry or directory is False. An error that does not say 'absent' is True: when the
    file system cannot answer, the call must stop.
    """
    try:
        os.lstat(Path(out_dir) / KILL_SWITCH_FILE_NAME)
    except (FileNotFoundError, NotADirectoryError):
        return False
    except OSError:
        return True
    return True


def _text(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _check_arguments(now_utc: datetime, min_interval_ms: int) -> None:
    if not isinstance(now_utc, datetime) or now_utc.tzinfo is None or now_utc.utcoffset() is None:
        raise ValueError("now_utc: not an aware datetime")
    if type(min_interval_ms) is not int or min_interval_ms < 0:
        raise ValueError("min_interval_ms: not a non-negative integer")


def _no_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    found: dict[str, object] = {}
    for key, value in pairs:
        if key in found:
            raise ValueError("duplicate key")
        found[key] = value
    return found


def _parse(data: bytes) -> datetime | None:
    """The UTC instant of a record, or None for an empty file; ``call_lock_invalid`` otherwise."""
    if not data:
        return None
    try:
        text = data.decode("utf-8")
        if not text.endswith("\n") or text.count("\n") != 1:
            raise ValueError("not one line")
        document = json.loads(text, object_pairs_hook=_no_duplicates)
        if not isinstance(document, dict) or set(document) != {"format", "monotonic_ns", "utc"}:
            raise ValueError("keys")
        mono = document["monotonic_ns"]
        stamp = document["utc"]
        if (
            document["format"] != CALL_LOCK_FORMAT
            or type(mono) is not int
            or not 0 <= mono <= _MAX_MONOTONIC
            or not isinstance(stamp, str)
            or _UTC.fullmatch(stamp) is None
        ):
            raise ValueError("values")
        return datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)
    except (ValueError, UnicodeDecodeError, RecursionError):
        raise GuardError("call_lock_invalid") from None


def _judge(last: datetime | None, now_utc: datetime, min_interval_ms: int) -> None:
    if last is None or min_interval_ms == 0:
        return
    now = now_utc.astimezone(UTC)
    if now < last:
        raise GuardError("call_lock_clock_inconsistent")
    if now - last < timedelta(milliseconds=min_interval_ms):
        raise GuardError("min_call_interval_not_elapsed")


def _read_checked(descriptor: int) -> bytes:
    info = os.fstat(descriptor)
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
        raise GuardError("call_lock_invalid")
    data = b""
    while len(data) <= MAX_CALL_LOCK_BYTES:
        chunk = os.read(descriptor, MAX_CALL_LOCK_BYTES + 1 - len(data))
        if not chunk:
            break
        data += chunk
    if len(data) > MAX_CALL_LOCK_BYTES:
        raise GuardError("call_lock_invalid")
    return data


def check_call_interval(
    out_dir: str | os.PathLike[str], *, now_utc: datetime, min_interval_ms: int
) -> None:
    """Step 2: read-only. An absent directory or file means no earlier call."""
    _check_arguments(now_utc, min_interval_ms)
    path = Path(out_dir) / CALL_LOCK_FILE_NAME
    try:
        descriptor = os.open(path, os.O_RDONLY | _NOFOLLOW | _CLOEXEC | os.O_NONBLOCK)
    except (FileNotFoundError, NotADirectoryError):
        return
    except OSError:
        raise GuardError("call_lock_invalid") from None
    try:
        data = _read_checked(descriptor)
    finally:
        os.close(descriptor)
    _judge(_parse(data), now_utc, min_interval_ms)


class CallLock:
    """The claim on ``call.lock``: held until ``close`` (or the end of the ``with`` block)."""

    def __init__(self, descriptor: int) -> None:
        self._descriptor: int | None = descriptor

    @classmethod
    def claim(
        cls,
        out_dir: str | os.PathLike[str],
        *,
        now_utc: datetime,
        monotonic_ns: int,
        min_interval_ms: int,
    ) -> CallLock:
        _check_arguments(now_utc, min_interval_ms)
        if type(monotonic_ns) is not int or not 0 <= monotonic_ns <= _MAX_MONOTONIC:
            raise ValueError("monotonic_ns: not a non-negative integer")
        path = Path(out_dir) / CALL_LOCK_FILE_NAME
        flags = os.O_RDWR | os.O_CREAT | _NOFOLLOW | _CLOEXEC | os.O_NONBLOCK
        try:
            descriptor = os.open(path, flags, 0o600)
        except OSError:
            raise GuardError("call_lock_invalid") from None
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
                raise GuardError("call_lock_invalid")
            if stat.S_IMODE(info.st_mode) != 0o600:
                os.fchmod(descriptor, 0o600)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise GuardError("call_in_progress") from None
            _judge(_parse(_read_checked(descriptor)), now_utc, min_interval_ms)
            record = json.dumps(
                {
                    "format": CALL_LOCK_FORMAT,
                    "monotonic_ns": monotonic_ns,
                    "utc": _text(now_utc),
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            os.ftruncate(descriptor, 0)
            os.lseek(descriptor, 0, os.SEEK_SET)
            os.write(descriptor, (record + "\n").encode("ascii"))
            os.fsync(descriptor)
        except BaseException:
            os.close(descriptor)
            raise
        return cls(descriptor)

    def close(self) -> None:
        if self._descriptor is not None:
            os.close(self._descriptor)  # closing releases the flock
            self._descriptor = None

    def __enter__(self) -> CallLock:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
