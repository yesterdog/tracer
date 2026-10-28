# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""BL-355 / audit A-F6: the target allowlist is read under the rules of the judge allowlist.

Contract: docs/phase4/api-surface-batch-g2.md section 4. The judge-allowlist reader refuses a link,
a hard link, a non-regular file, a file with a group or other permission bit and a file past the
size cap, and opens without following a link (``O_NOFOLLOW``, non-blocking). The target-allowlist
reader (ADR 0026 rule 16) examined the path with ``lstat`` and then opened it again by name with
``open``, so a same-user race (a link, a FIFO or another file swapped in between the two calls)
gave it any file at all, and it never checked the link count. Section 4.1 lists the rules and the
stable codes (the codes are the ones this reader already had); 4.2 the swap races, each simulated
by running the swap just before the ``os.open`` of the path (the instant after any check of the
path, whatever the implementation checks first); 4.3 the shared helper.

No test opens a socket.
"""

from __future__ import annotations

import os
import stat
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from evalsim_support_imports import mod  # type: ignore[import-not-found,unused-ignore]

ENTRY = "ws://localhost/ws auth_token_env=AGENT_BEARER\n"
NORMALISED = "ws://localhost:80/ws"


def al() -> Any:
    return mod("allowlist")


def code_of(function: Callable[[], Any]) -> str:
    with pytest.raises(al().AllowlistError) as caught:
        function()
    error = caught.value
    assert str(error) == error.code  # the message is the code and nothing else
    return str(error.code)


def good_file(directory: Path, name: str = "allow.txt", text: str = ENTRY) -> Path:
    path = directory / name
    path.write_text(text, "utf-8")
    path.chmod(0o600)
    return path


# ------------------------------------------------------------------------------------ 4.1


def test_a_private_regular_file_is_read(tmp_path: Path) -> None:
    """BL-355 / A-F6 (4.1): the happy path is unchanged (mode 0600, and 0400 as before)."""
    path = good_file(tmp_path)
    (entry,) = al().read_allowlist(path)
    assert (entry.target, entry.auth_token_env) == (NORMALISED, "AGENT_BEARER")
    for mode in (0o400, 0o500, 0o700):  # only the group and other bits are forbidden
        path.chmod(mode)
        assert len(al().read_allowlist(path)) == 1, oct(mode)


@pytest.mark.parametrize(
    "mode",
    [0o640, 0o644, 0o660, 0o666, 0o604, 0o602, 0o610, 0o601, 0o670],
    ids=lambda mode: f"mode_{mode:o}",
)
def test_any_group_or_other_permission_bit_is_refused(tmp_path: Path, mode: int) -> None:
    """BL-355 / A-F6 (4.1): the judge rule, bit by bit: ``mode & 0o077`` refuses."""
    path = good_file(tmp_path)
    path.chmod(mode)
    assert code_of(lambda: al().read_allowlist(path)) == "allowlist_mode_too_open"


def test_a_symbolic_link_is_refused(tmp_path: Path) -> None:
    """BL-355 / A-F6 (4.1)."""
    real = good_file(tmp_path)
    link = tmp_path / "link.txt"
    os.symlink(real, link)
    assert code_of(lambda: al().read_allowlist(link)) == "allowlist_not_a_regular_file"
    dangling = tmp_path / "dangling.txt"
    os.symlink(tmp_path / "nowhere", dangling)
    assert code_of(lambda: al().read_allowlist(dangling)) == "allowlist_not_a_regular_file"


def test_a_hard_link_is_refused_under_both_names(tmp_path: Path) -> None:
    """BL-355 / A-F6 (4.1): ``st_nlink`` must be 1; once the second name is removed the file reads
    again."""
    real = good_file(tmp_path)
    other = tmp_path / "other-name.txt"
    os.link(real, other)
    assert os.stat(real).st_nlink == 2
    assert code_of(lambda: al().read_allowlist(real)) == "allowlist_not_a_regular_file"
    assert code_of(lambda: al().read_allowlist(other)) == "allowlist_not_a_regular_file"
    other.unlink()
    assert len(al().read_allowlist(real)) == 1


def test_a_fifo_a_directory_and_a_missing_path_are_refused(tmp_path: Path) -> None:
    """BL-355 / A-F6 (4.1): the FIFO is refused without being opened (an open would block)."""
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo, 0o600)
    assert code_of(lambda: al().read_allowlist(fifo)) == "allowlist_not_a_regular_file"
    assert code_of(lambda: al().read_allowlist(tmp_path)) == "allowlist_not_a_regular_file"
    assert code_of(lambda: al().read_allowlist(tmp_path / "nope")) == "allowlist_unreadable"


def test_the_size_cap_is_inclusive_and_counts_the_bytes_read(tmp_path: Path) -> None:
    """BL-355 / A-F6 (4.1): exactly ``MAX_ALLOWLIST_BYTES`` reads, one byte more is refused, and a
    file that is not UTF-8 is unreadable."""
    cap = al().MAX_ALLOWLIST_BYTES
    assert cap == 65_536
    exact = good_file(tmp_path, "exact.txt", "# " + "x" * (cap - 2))
    assert al().read_allowlist(exact) == ()
    over = good_file(tmp_path, "over.txt", "# " + "x" * (cap - 1))
    assert code_of(lambda: al().read_allowlist(over)) == "allowlist_too_large"
    odd = tmp_path / "odd.txt"
    odd.write_bytes(b"\xff\xfe\x00")
    odd.chmod(0o600)
    assert code_of(lambda: al().read_allowlist(odd)) == "allowlist_unreadable"


# ------------------------------------------------------------------------------------ 4.2


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


def test_a_link_swapped_in_before_the_open_is_not_followed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """BL-355 / A-F6 (4.2, ``O_NOFOLLOW``): the path was a private regular file and is a link to
    another private regular file when it is opened: refused, ``allowlist_not_a_regular_file`` (the
    open fails with ``ELOOP``, which is a link, as it is for a link that was there all along)."""
    path = good_file(tmp_path)
    target = good_file(tmp_path, "target.txt", "ws://localhost:9999/evil\n")

    def swap() -> None:
        path.unlink()
        os.symlink(target, path)

    ran = before_open(monkeypatch, path, swap)
    assert code_of(lambda: al().read_allowlist(path)) == "allowlist_not_a_regular_file"
    assert ran


def test_a_fifo_swapped_in_before_the_open_is_refused_without_blocking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """BL-355 / A-F6 (4.2, ``O_NONBLOCK``): the path is a FIFO with no writer when it is opened. A
    blocking open would hang for ever; the open does not block and the descriptor says FIFO. The
    call runs in a thread so that a hang fails the test instead of stopping the run."""
    path = good_file(tmp_path)

    def swap() -> None:
        path.unlink()
        os.mkfifo(path, 0o600)

    ran = before_open(monkeypatch, path, swap)
    outcome: list[Any] = []

    def run() -> None:
        try:
            outcome.append(al().read_allowlist(path))
        except BaseException as error:
            outcome.append(error)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(5)
    if thread.is_alive():  # release a blocked open so that the test process can end
        release = os.open(path, os.O_RDWR | os.O_NONBLOCK)
        thread.join(5)
        os.close(release)
        pytest.fail("the open of a FIFO blocked")
    (result,) = outcome
    assert isinstance(result, al().AllowlistError), result
    assert result.code == "allowlist_not_a_regular_file"
    assert ran


def test_a_mode_widened_before_the_open_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """BL-355 / A-F6 (4.2): the mode is judged on the file that is held, not on an earlier look at
    the path: ``allowlist_mode_too_open``."""
    path = good_file(tmp_path)
    ran = before_open(monkeypatch, path, lambda: path.chmod(0o644))
    assert code_of(lambda: al().read_allowlist(path)) == "allowlist_mode_too_open"
    assert ran


def test_a_second_name_made_before_the_open_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """BL-355 / A-F6 (4.2): the link count is judged on the file that is held."""
    path = good_file(tmp_path)
    ran = before_open(monkeypatch, path, lambda: os.link(path, tmp_path / "second-name"))
    assert code_of(lambda: al().read_allowlist(path)) == "allowlist_not_a_regular_file"
    assert ran


def test_a_file_grown_before_the_open_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """BL-355 / A-F6 (4.2): a file that was small and is past the cap when it is held:
    ``allowlist_too_large``."""
    path = good_file(tmp_path)
    cap = al().MAX_ALLOWLIST_BYTES
    ran = before_open(monkeypatch, path, lambda: path.write_text("# " + "x" * cap))
    assert code_of(lambda: al().read_allowlist(path)) == "allowlist_too_large"
    assert ran


def test_the_size_cap_binds_the_bytes_read_not_the_size_that_fstat_reports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """BL-355 / A-F6 (4.2, size): a file that grows after it was measured (simulated by an ``fstat``
    that reports 10 bytes for the large file) is refused by the bytes actually read: at most the cap
    plus one byte are read, ``allowlist_too_large``."""
    cap = al().MAX_ALLOWLIST_BYTES
    path = good_file(tmp_path, "grown.txt", "# " + "x" * (cap + 100))
    real = os.fstat

    def fstat(descriptor: int) -> os.stat_result:
        info = real(descriptor)
        if info.st_size > cap:
            fields = list(info)
            fields[stat.ST_SIZE] = 10
            return os.stat_result(fields)
        return info

    monkeypatch.setattr(os, "fstat", fstat)
    assert code_of(lambda: al().read_allowlist(path)) == "allowlist_too_large"


def test_the_descriptor_is_opened_read_only_without_following_and_without_blocking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """BL-355 / A-F6 (4.2): the flags of the one ``os.open`` of the reader include ``O_RDONLY``,
    ``O_NOFOLLOW`` and ``O_NONBLOCK`` (``O_CLOEXEC`` may be added), and every descriptor it opens is
    closed, on the way out with an entry and on the way out with a refusal."""
    path = good_file(tmp_path)
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
    assert len(al().read_allowlist(path)) == 1
    path.chmod(0o644)
    assert code_of(lambda: al().read_allowlist(path)) == "allowlist_mode_too_open"
    assert len(seen) == 2
    for flags in seen:
        assert flags & os.O_ACCMODE == os.O_RDONLY
        assert flags & os.O_NOFOLLOW
        assert flags & os.O_NONBLOCK
    assert set(opened) <= set(closed)


def test_a_refusal_names_no_path_and_no_content(tmp_path: Path) -> None:
    """BL-355 / A-F6: the error is the code; the path and the file text appear nowhere in it."""
    secret_dir = tmp_path / "very-private-directory-name"
    secret_dir.mkdir()
    path = good_file(secret_dir, "a.txt", "ws://localhost/ws auth_token_env=BAD NAME\n")
    path.chmod(0o640)
    with pytest.raises(al().AllowlistError) as caught:
        al().read_allowlist(path)
    text = repr(caught.value) + str(caught.value) + "".join(map(str, caught.value.args))
    assert "very-private-directory-name" not in text
    assert "ws://localhost" not in text
    assert caught.value.__cause__ is None


# ------------------------------------------------------------------------------------ 4.3


def test_the_target_reader_is_built_on_the_shared_private_file_reader() -> None:
    """BL-355 / A-F6 (4.3): ``read_allowlist`` does not carry its own copy of the file checks: it
    calls ``tracer_evalcore.privatefile.read_private_file`` (the helper the judge reader calls
    too), so the two readers cannot drift apart again."""
    import ast

    import tracer_evalsim.allowlist as module

    tree = ast.parse(Path(module.__file__).read_text("utf-8"))
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} | {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    }
    assert "read_private_file" in names
    assert "lstat" not in names  # the examination lives in the shared helper
