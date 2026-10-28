# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Read a small private file the way the owner's allowlists must be read (ADR 0024 amendment A12,
ADR 0026 rule 16, ADR 0030 rule 14; BL-355).

The file is opened first, without following a link and without blocking (``O_NOFOLLOW`` and
``O_NONBLOCK``), and everything is judged on the open descriptor with ``fstat``: there is no
examination of the path before the open, so no file can be swapped in between a check and the read.
It must be a regular file with exactly one name (``st_nlink == 1``) and no group or other permission
bit, and at most ``max_bytes`` long; the bytes actually read bind the size, not the size that was
examined. A link is ``not_a_regular_file`` (the open fails with ``ELOOP``), a missing path or any
other failure is ``unreadable``. An error holds a stable code and nothing else: no path, no content.
"""

from __future__ import annotations

import errno
import os
import stat
from typing import Final

from tracer_evalcore.errors import EvalcoreError

__all__ = ["PRIVATE_FILE_REASONS", "PrivateFileError", "read_private_file"]

PRIVATE_FILE_REASONS: Final = ("mode_too_open", "not_a_regular_file", "too_large", "unreadable")
_FLAGS: Final = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0)


class PrivateFileError(EvalcoreError):
    """A private file was refused; ``code`` is one of ``PRIVATE_FILE_REASONS`` and the message."""

    def __init__(self, code: str) -> None:
        if code not in PRIVATE_FILE_REASONS:
            raise ValueError("code: not a reason")
        self.code = code
        super().__init__(code)


def _read_all(descriptor: int, limit: int) -> bytes:
    chunks: list[bytes] = []
    remaining = limit
    while remaining > 0:
        chunk = os.read(descriptor, remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _judge(descriptor: int, max_bytes: int) -> bytes:
    info = os.fstat(descriptor)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise PrivateFileError("not_a_regular_file")
    if info.st_mode & 0o077:
        raise PrivateFileError("mode_too_open")
    if info.st_size > max_bytes:
        raise PrivateFileError("too_large")
    data = _read_all(descriptor, max_bytes + 1)
    if len(data) > max_bytes:
        raise PrivateFileError("too_large")
    return data


def read_private_file(path: str | os.PathLike[str], *, max_bytes: int) -> bytes:
    """The bytes of the file, or a ``PrivateFileError``."""
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 1:
        raise ValueError("max_bytes: not a positive integer")
    try:
        descriptor = os.open(path, _FLAGS)
    except OSError as error:
        if error.errno == errno.ELOOP:
            raise PrivateFileError("not_a_regular_file") from None
        raise PrivateFileError("unreadable") from None
    try:
        return _judge(descriptor, max_bytes)
    except OSError:
        raise PrivateFileError("unreadable") from None
    finally:
        os.close(descriptor)
