# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""BL-355 / audit A-F6: the one reader of the owner's private files (the two allowlists).

Contract: docs/phase4/api-surface-batch-g2.md section 4.3. ``tracer_evalcore.privatefile`` holds the
rules that the judge-allowlist reader had and the target-allowlist reader lacked, once: a regular
file, one name (``st_nlink == 1``), no group or other permission bit, a size cap that binds the
bytes read, opened read-only with ``O_NOFOLLOW`` and ``O_NONBLOCK`` and judged on the open
descriptor (``fstat``), with no look at the path before the open. Its error holds a stable code
and nothing else. The readers of the two allowlists map each code with the ``allowlist_`` prefix
(their own tests pin that). No test opens a socket.
"""

from __future__ import annotations

import importlib
import os
import stat
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest


def pf() -> Any:
    return importlib.import_module("tracer_evalcore.privatefile")


def code_of(path: Path, limit: int = 100) -> str:
    with pytest.raises(pf().PrivateFileError) as caught:
        pf().read_private_file(path, max_bytes=limit)
    error = caught.value
    assert str(error) == error.code
    assert error.__cause__ is None
    return str(error.code)


def good(directory: Path, name: str = "f", data: bytes = b"hello\n") -> Path:
    path = directory / name
    path.write_bytes(data)
    path.chmod(0o600)
    return path


def before_open(
    monkeypatch: pytest.MonkeyPatch, path: Path, action: Callable[[], object]
) -> list[int]:
    """Run ``action`` just before the first ``os.open`` of ``path`` (the real open follows): the
    state after any check of the path and before the file is held. The list says it ran."""
    real = os.open
    ran: list[int] = []

    def opener(target: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        if not ran and os.fspath(target) == os.fspath(path):
            ran.append(1)
            action()
        return real(target, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", opener)
    return ran


def test_the_reasons_are_a_closed_sorted_tuple_and_the_error_is_an_evalcore_error() -> None:
    """BL-355 / A-F6 (4.3)."""
    module = pf()
    assert module.PRIVATE_FILE_REASONS == (
        "mode_too_open",
        "not_a_regular_file",
        "too_large",
        "unreadable",
    )
    assert list(module.PRIVATE_FILE_REASONS) == sorted(module.PRIVATE_FILE_REASONS)
    assert issubclass(
        module.PrivateFileError, importlib.import_module("tracer_evalcore.errors").EvalcoreError
    )
    assert set(module.__all__) == {"PRIVATE_FILE_REASONS", "PrivateFileError", "read_private_file"}
    with pytest.raises(ValueError, match="code"):
        module.PrivateFileError("anything_else")


def test_a_private_regular_file_is_read_exactly(tmp_path: Path) -> None:
    """BL-355 / A-F6 (4.3): the bytes, nothing decoded; mode 0600 and 0400 both read."""
    path = good(tmp_path, data=b"\xff\x00abc")
    assert pf().read_private_file(path, max_bytes=100) == b"\xff\x00abc"
    for mode in (0o400, 0o500, 0o700):  # only the group and other bits are forbidden
        path.chmod(mode)
        assert pf().read_private_file(str(path), max_bytes=100) == b"\xff\x00abc", oct(mode)
    assert pf().read_private_file(path, max_bytes=6) == b"\xff\x00abc"  # the cap is inclusive


def test_the_cap_is_inclusive_and_binds_the_bytes_read(tmp_path: Path) -> None:
    """BL-355 / A-F6 (4.3)."""
    assert code_of(good(tmp_path, data=b"x" * 101), 100) == "too_large"
    assert pf().read_private_file(good(tmp_path, "a", b"x" * 100), max_bytes=100) == b"x" * 100


@pytest.mark.parametrize(
    "mode",
    [0o640, 0o644, 0o660, 0o666, 0o604, 0o602, 0o610, 0o601, 0o670],
    ids=lambda mode: f"mode_{mode:o}",
)
def test_a_group_or_other_bit_is_refused(tmp_path: Path, mode: int) -> None:
    """BL-355 / A-F6 (4.3)."""
    path = good(tmp_path)
    path.chmod(mode)
    assert code_of(path) == "mode_too_open"


def test_links_fifos_directories_and_missing_paths(tmp_path: Path) -> None:
    """BL-355 / A-F6 (4.3)."""
    real = good(tmp_path)
    link = tmp_path / "link"
    link.symlink_to(real)
    assert code_of(link) == "not_a_regular_file"
    hard = tmp_path / "hard"
    os.link(real, hard)
    assert code_of(real) == "not_a_regular_file"
    assert code_of(hard) == "not_a_regular_file"
    hard.unlink()
    assert pf().read_private_file(real, max_bytes=100) == b"hello\n"
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo, 0o600)
    assert code_of(fifo) == "not_a_regular_file"
    assert code_of(tmp_path) == "not_a_regular_file"
    assert code_of(tmp_path / "nowhere") == "unreadable"


def test_a_link_swapped_in_before_the_open_is_not_followed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """BL-355 / A-F6 (4.2, ``O_NOFOLLOW``): the path was a private regular file and is a link to
    another private regular file when it is opened: ``not_a_regular_file`` (the open fails with
    ``ELOOP``), the same code as for a link that was there all along."""
    path = good(tmp_path)
    target = good(tmp_path, "target", b"evil")

    def swap() -> None:
        path.unlink()
        os.symlink(target, path)

    ran = before_open(monkeypatch, path, swap)
    assert code_of(path) == "not_a_regular_file"
    assert ran


def test_a_fifo_swapped_in_before_the_open_does_not_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """BL-355 / A-F6 (4.2, ``O_NONBLOCK``): run in a thread so that a hang fails the test."""
    path = good(tmp_path)

    def swap() -> None:
        path.unlink()
        os.mkfifo(path, 0o600)

    ran = before_open(monkeypatch, path, swap)
    outcome: list[Any] = []

    def run() -> None:
        try:
            outcome.append(pf().read_private_file(path, max_bytes=100))
        except BaseException as error:
            outcome.append(error)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(5)
    if thread.is_alive():
        release = os.open(path, os.O_RDWR | os.O_NONBLOCK)
        thread.join(5)
        os.close(release)
        pytest.fail("the open of a FIFO blocked")
    (result,) = outcome
    assert isinstance(result, pf().PrivateFileError), result
    assert result.code == "not_a_regular_file"
    assert ran


def test_a_mode_widened_before_the_open_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """BL-355 / A-F6 (4.2): the mode is judged on the file that is held."""
    path = good(tmp_path)
    ran = before_open(monkeypatch, path, lambda: path.chmod(0o644))
    assert code_of(path) == "mode_too_open"
    assert ran


def test_a_second_name_made_before_the_open_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """BL-355 / A-F6 (4.2): the link count is judged on the file that is held."""
    path = good(tmp_path)
    ran = before_open(monkeypatch, path, lambda: os.link(path, tmp_path / "second-name"))
    assert code_of(path) == "not_a_regular_file"
    assert ran


def test_a_file_grown_before_the_open_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """BL-355 / A-F6 (4.2)."""
    path = good(tmp_path)
    ran = before_open(monkeypatch, path, lambda: path.write_bytes(b"x" * 500))
    assert code_of(path, 100) == "too_large"
    assert ran


def test_the_size_that_fstat_reports_does_not_bind_the_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """BL-355 / A-F6 (4.2): a file that grows after it was measured (an ``fstat`` that reports 3
    bytes for the 500 that are there) is refused by the bytes read."""
    path = good(tmp_path, "grown", b"x" * 500)
    real = os.fstat

    def fstat(descriptor: int) -> os.stat_result:
        info = real(descriptor)
        if info.st_size == 500:
            fields = list(info)
            fields[stat.ST_SIZE] = 3
            return os.stat_result(fields)
        return info

    monkeypatch.setattr(os, "fstat", fstat)
    assert code_of(path, 100) == "too_large"


def test_a_file_past_the_cap_is_refused_before_it_is_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """BL-355 / A-F6 (4.1): the size that ``fstat`` reports refuses a large file without reading a
    byte of it (a file of any size is never pulled into memory to be refused)."""
    path = good(tmp_path, "large", b"x" * 500)
    reads: list[int] = []
    real = os.read

    def spy(descriptor: int, count: int) -> bytes:
        reads.append(count)
        return real(descriptor, count)

    monkeypatch.setattr(os, "read", spy)
    assert code_of(path, 100) == "too_large"
    assert reads == []


def test_the_flags_of_the_open_and_the_close_of_the_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """BL-355 / A-F6 (4.2): one open per call, with ``O_RDONLY``, ``O_NOFOLLOW`` and ``O_NONBLOCK``
    (``O_CLOEXEC`` may be added), and the descriptor is closed also when the file is refused."""
    path = good(tmp_path)
    seen: list[int] = []
    opened: list[int] = []
    closed: list[int] = []
    real_open, real_close = os.open, os.close

    def spy_open(target: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        descriptor = real_open(target, flags, *args, **kwargs)
        if os.fspath(target) == os.fspath(path):
            seen.append(flags)
            opened.append(descriptor)
        return descriptor

    def spy_close(descriptor: int) -> None:
        closed.append(descriptor)
        real_close(descriptor)

    monkeypatch.setattr(os, "open", spy_open)
    monkeypatch.setattr(os, "close", spy_close)
    pf().read_private_file(path, max_bytes=100)
    path.chmod(0o644)
    assert code_of(path) == "mode_too_open"
    assert len(seen) == 2
    for flags in seen:
        assert flags & os.O_ACCMODE == os.O_RDONLY
        assert flags & os.O_NOFOLLOW
        assert flags & os.O_NONBLOCK
    assert set(opened) <= set(closed)


@pytest.mark.parametrize("bad", [0, -1, True, 1.5, "10", None])
def test_max_bytes_must_be_a_positive_integer(tmp_path: Path, bad: Any) -> None:
    """BL-355 / A-F6 (4.3): ``ValueError``; nothing is guessed."""
    with pytest.raises(ValueError, match="max_bytes"):
        pf().read_private_file(good(tmp_path), max_bytes=bad)


def test_the_error_names_no_path_and_no_content(tmp_path: Path) -> None:
    """BL-355 / A-F6 (4.3)."""
    folder = tmp_path / "very-private-folder"
    folder.mkdir()
    path = good(folder, "secret-name", b"secret-content")
    path.chmod(0o644)
    with pytest.raises(pf().PrivateFileError) as caught:
        pf().read_private_file(path, max_bytes=100)
    text = repr(caught.value) + str(caught.value)
    assert "very-private-folder" not in text
    assert "secret" not in text
