# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P4-31 (``test_allowlist_mode_and_link_rules``, the open of the file): the allowlist is read
without following a link and judged on the descriptor it was opened as (ADR 0026 rule 16).

Contract: docs/phase4/api-surface-batch-g.md section 6. Reading the file is a check and then an
open, and between the two the path can be swapped (a time-of-check, time-of-use race): the mode,
the type, the size and the link rule must be decided on the opened descriptor (``O_NOFOLLOW``,
``fstat``), not on a ``lstat`` made earlier. The tests make ``os.lstat`` lie, as a swap would, and
require that the answer does not change. A file that is not a regular file is refused without being
opened for a blocking read.
"""

from __future__ import annotations

import os
import signal
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from evalsim_support_g import mod  # type: ignore[import-not-found,unused-ignore]

ENTRY = "ws://127.0.0.1:8765/ws-client\n"


def allowlist() -> Any:
    return mod("allowlist")


def code_of(path: Path) -> str:
    with pytest.raises(allowlist().AllowlistError) as caught:
        allowlist().read_allowlist(path)
    assert str(caught.value) == caught.value.code
    return str(caught.value.code)


@pytest.fixture
def alarm() -> Iterator[None]:
    """A test that would block (a FIFO opened for reading) fails after five seconds instead."""

    def fired(signum: int, frame: Any) -> None:
        raise AssertionError("reading the allowlist blocked")

    old = signal.signal(signal.SIGALRM, fired)
    signal.alarm(5)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)


def good(tmp_path: Path, name: str = "allowlist.txt", mode: int = 0o600) -> Path:
    path = tmp_path / name
    path.write_text(ENTRY, "ascii")
    path.chmod(mode)
    return path


def lying_lstat(path: Path, *, mode: int, size: int) -> Any:
    """``os.lstat`` that reports a private regular file of ``size`` bytes for ``path`` (what a
    swap after the check looks like) and the truth for any other path."""
    real = os.lstat
    wanted = str(path)

    def lstat(target: Any, *args: Any, **kwargs: Any) -> Any:
        info = real(target, *args, **kwargs)
        if str(target) != wanted:
            return info
        fields = list(info)
        fields[0] = mode  # st_mode
        fields[6] = size  # st_size
        return os.stat_result(fields)

    return lstat


def test_a_link_swapped_in_after_the_check_is_still_not_followed(tmp_path: Path) -> None:
    """The path is a symbolic link to a good private allowlist. Whatever an earlier ``lstat``
    claimed, the open itself must refuse a link (``O_NOFOLLOW``): the entries behind it are never
    returned."""
    target = good(tmp_path, "real.txt")
    link = tmp_path / "allowlist.txt"
    os.symlink(target, link)
    assert code_of(link) == "allowlist_not_a_regular_file"  # the plain case
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(os, "lstat", lying_lstat(link, mode=0o100600, size=len(ENTRY)))
        assert code_of(link) == "allowlist_not_a_regular_file"


def test_a_mode_widened_after_the_check_is_still_refused(tmp_path: Path) -> None:
    """The file is 0644 now; a stale answer said 0600. The mode is read from the descriptor."""
    path = good(tmp_path, mode=0o644)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(os, "lstat", lying_lstat(path, mode=0o100600, size=len(ENTRY)))
        assert code_of(path) == "allowlist_mode_too_open"


def test_a_file_grown_after_the_check_is_refused_by_what_was_read(tmp_path: Path) -> None:
    """The size cap is 65,536 bytes. The stale answer said 29 bytes; the file has 70,000. At most
    one byte over the cap is read and a longer file is ``allowlist_too_large``, never parsed as far
    as it was read."""
    path = tmp_path / "allowlist.txt"
    path.write_text(ENTRY + "# " + "x" * 70_000 + "\n", "ascii")
    path.chmod(0o600)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(os, "lstat", lying_lstat(path, mode=0o100600, size=len(ENTRY)))
        assert code_of(path) == "allowlist_too_large"
    assert code_of(path) == "allowlist_too_large"


def test_a_file_that_grows_after_it_was_measured_is_refused_by_what_was_read(
    tmp_path: Path,
) -> None:
    """The descriptor itself can lie about its size (a file that grows between the ``fstat`` and
    the read): the length actually read decides, not the size that was reported."""
    path = tmp_path / "allowlist.txt"
    path.write_text(ENTRY + "# " + "x" * 70_000 + "\n", "ascii")
    path.chmod(0o600)
    real = os.fstat

    def small(descriptor: int) -> Any:
        info = real(descriptor)
        fields = list(info)
        fields[6] = len(ENTRY)  # st_size
        return os.stat_result(fields)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(os, "fstat", small)
        assert code_of(path) == "allowlist_too_large"


def test_at_most_one_byte_over_the_cap_is_ever_read(tmp_path: Path) -> None:
    """A hostile or huge file is not slurped: whatever its size, no more than ``MAX + 1`` bytes are
    read from it (the byte over the cap is what proves it too large)."""
    path = tmp_path / "allowlist.txt"
    path.write_text(ENTRY + "# " + "x" * 3_000_000 + "\n", "ascii")
    path.chmod(0o600)
    real_read = os.read
    real_fstat = os.fstat
    taken = [0]

    def counting(descriptor: int, size: int) -> bytes:
        data = real_read(descriptor, size)
        taken[0] += len(data)
        return data

    def small(descriptor: int) -> Any:  # a reported size that would not stop the read early
        fields = list(real_fstat(descriptor))
        fields[6] = len(ENTRY)  # st_size
        return os.stat_result(fields)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(os, "read", counting)
        patch.setattr(os, "fstat", small)
        assert code_of(path) == "allowlist_too_large"
    assert 0 < taken[0] <= allowlist().MAX_ALLOWLIST_BYTES + 1


def test_a_file_of_exactly_the_cap_is_read(tmp_path: Path) -> None:
    limit = allowlist().MAX_ALLOWLIST_BYTES
    filler = "#" * (limit - len(ENTRY) - 1) + "\n"
    path = tmp_path / "allowlist.txt"
    path.write_text(ENTRY + filler, "ascii")
    path.chmod(0o600)
    assert path.stat().st_size == limit
    entries = allowlist().read_allowlist(path)
    assert [e.target for e in entries] == ["ws://127.0.0.1:8765/ws-client"]
    path.write_text(ENTRY + filler + "#", "ascii")
    path.chmod(0o600)
    assert code_of(path) == "allowlist_too_large"


def test_a_fifo_is_refused_without_a_blocking_read(tmp_path: Path, alarm: None) -> None:
    fifo = tmp_path / "allowlist.txt"
    os.mkfifo(fifo, 0o600)
    assert code_of(fifo) == "allowlist_not_a_regular_file"
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(os, "lstat", lying_lstat(fifo, mode=0o100600, size=0))
        assert code_of(fifo) == "allowlist_not_a_regular_file"


def test_the_good_file_still_reads_and_a_directory_or_a_missing_file_has_its_code(
    tmp_path: Path,
) -> None:
    path = good(tmp_path)
    assert [e.target for e in allowlist().read_allowlist(path)] == ["ws://127.0.0.1:8765/ws-client"]
    assert code_of(tmp_path / "missing.txt") == "allowlist_unreadable"
    directory = tmp_path / "dir"
    directory.mkdir(mode=0o700)
    assert code_of(directory) == "allowlist_not_a_regular_file"
    assert [e.target for e in allowlist().read_allowlist(str(path))] == [
        "ws://127.0.0.1:8765/ws-client"
    ]
