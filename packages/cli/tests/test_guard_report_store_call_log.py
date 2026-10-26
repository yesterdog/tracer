# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P3-34: the repository guard refuses a staged report, store file or call log of ``tracer score``.

The three outputs hold personal data (the call text, the call audio location, the call id). The
command writes them to a user-chosen ``--out`` directory; this guard and the ``.gitignore`` entries
keep them out of the repository (SCOPE Q17). The real ``scripts/guard_private_fixtures.py`` runs in
a throwaway repository (the project repository is never touched), as the Phase 1 guard test does.
Contract: docs/phase3/api-surface-batch-f.md section 6.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from cli_batchf_support import (  # type: ignore[import-not-found,unused-ignore]
    GIT,
    GUARD_SCRIPT,
    ROOT,
    git,
    guard_repo,
    run_guard,
)

PRIVATE_PREFIX = "packages/evalschema/tests/fixtures/private/"

REPORT_MESSAGE = "tracer report file tracked, staged or in history: "
STORE_MESSAGE = "tracer store file tracked, staged or in history: "
CALL_LOG_MESSAGE = "tracer call log tracked, staged or in history: "
LEGACY_CALL_LOG = "tools/capture/sarvam_call_log.jsonl"

MARKER = '<meta name="tracer-report" content="1">'
#: The head of a report of batch E (contract 5.1), reduced to what the guard may read.
REPORT_HTML = (
    "<!DOCTYPE html>\n"
    '<html lang="en"><head>\n<meta charset="utf-8">\n'
    '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'">\n'
    f"{MARKER}\n<title>x</title></head>\n"
    '<body><main id="report">x</main></body></html>\n'
)
SQLITE_MAGIC = b"SQLite format 3\x00"
#: A call log line as ``tracer_evalproviders.calllog`` writes it (integers and short keys only).
CALL_LOG_LINE = (
    '{"at":"2026-10-26T09:00:00.000000Z","phase":"before","chunk":0,"attempt":1,'
    '"bytes_sent":1103564,"audio_seconds":"22.99","status":null,"error_kind":null,'
    '"elapsed_ms":null,"request_id":null}\n'
)
#: The older log of the capture tool, tracked since Phase 1 (no text; a different line shape).
LEGACY_LINE = (
    '{"timestamp_utc": "2026-10-23T13:08:07.448937+00:00", "endpoint": "/text-to-speech", '
    '"case": "tts_en", "clip_id": "tts_en", "language_requested": "en-IN", "status": 200, '
    '"elapsed_s": 1.178}\n'
)


_repo = guard_repo
_guard = run_guard
_git = git


def _stage(repo: Path, path: str, data: bytes | str) -> None:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data.encode("utf-8") if isinstance(data, str) else data)
    _git(repo, "add", "-f", "--", path)


def _path_data_cases(rows: list[tuple[str, str | bytes]]) -> pytest.MarkDecorator:
    """Parameters ``(path, data)`` identified by the path (a 5 MB value is never an id)."""
    return pytest.mark.parametrize(
        ("path", "data"), [pytest.param(path, data, id=path) for path, data in rows]
    )


def _lines(result: subprocess.CompletedProcess[str]) -> list[str]:
    return result.stderr.splitlines()


def _refused_staged_and_tracked(repo: Path, path: str, message: str) -> None:
    """The guard names ``path`` while it is staged and again once it is committed (tracked)."""
    for state in ("staged", "tracked"):
        result = _guard(repo)
        assert result.returncode == 1, (state, path)
        assert message + path in _lines(result), (state, path, result.stderr)
        if state == "staged":
            _git(repo, "commit", "-q", "-m", "tracked")


# ---------------------------------------------------------------------- the named test


def test_guard_rejects_staged_report_store_and_call_log(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    assert _guard(repo).returncode == 0, "a clean repository passes"
    outputs: dict[str, tuple[str | bytes, str]] = {
        "out/report-20261026T090000Z.html": (REPORT_HTML, REPORT_MESSAGE),
        "out/tracer.sqlite": (SQLITE_MAGIC + b"\x00" * 100, STORE_MESSAGE),
        "out/sarvam_call_log.jsonl": (CALL_LOG_LINE, CALL_LOG_MESSAGE),
    }
    # written but not staged: not a finding (the files are git-ignored in real use)
    for path, (data, _message) in outputs.items():
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data.encode("utf-8") if isinstance(data, str) else data)
    assert _guard(repo).returncode == 0, "untracked outputs do not trip the guard"
    # staged, one at a time: exit 1, the path and the kind are named on stderr
    for path, (_data, message) in outputs.items():
        _git(repo, "add", "-f", "--", path)
        result = _guard(repo)
        assert result.returncode == 1, path
        assert message + path in _lines(result), (path, result.stderr)
        _git(repo, "rm", "-q", "--cached", "--", path)
        assert _guard(repo).returncode == 0, f"{path} unstaged again"
    # all three staged: all three reported, each on its own line
    for path in outputs:
        _git(repo, "add", "-f", "--", path)
    result = _guard(repo)
    assert result.returncode == 1
    for path, (_data, message) in outputs.items():
        assert message + path in _lines(result)
    # tracked after a commit: still refused
    _git(repo, "commit", "-q", "-m", "synthetic")
    tracked = _guard(repo)
    assert tracked.returncode == 1
    assert len(_lines(tracked)) == 3
    # removed from the index but still in history: refused by name
    for path in outputs:
        _git(repo, "rm", "-q", "--cached", "--", path)
    _git(repo, "commit", "-q", "-m", "untrack")
    history = _guard(repo)
    assert history.returncode == 1
    for path, (_data, message) in outputs.items():
        assert message + path in _lines(history)


# ---------------------------------------------------------------------- the reports


@pytest.mark.parametrize(
    "path",
    [
        "report-20261026T090000Z.html",
        "deep/er/out dir/report-20261231T235959Z.html",
        "caf\u00e9/report-20260101T000000Z.html",
    ],
)
def test_guard_refuses_a_report_by_its_name_whatever_it_holds(tmp_path: Path, path: str) -> None:
    repo = _repo(tmp_path)
    _stage(repo, path, "<html></html>\n")
    result = _guard(repo)
    assert result.returncode == 1
    assert REPORT_MESSAGE + path in _lines(result)


@_path_data_cases(
    [
        ("notes/share-me.html", REPORT_HTML),
        ("notes/share-me.HTM", REPORT_HTML),
        (
            "notes/spacing.html",
            REPORT_HTML.replace(MARKER, "<META  NAME = 'tracer-report'\ncontent=1>"),
        ),
        # a renamed report: not .html, but a document whose head carries the marker
        ("notes/renamed.txt", REPORT_HTML),
        ("notes/renamed", REPORT_HTML),
        # a report with audio embedded: megabytes after the head, refused all the same
        ("notes/big.html", REPORT_HTML.replace("x</main>", "A" * 5_000_000 + "</main>")),
    ],
)
def test_guard_refuses_a_report_by_the_marker_of_its_head(
    tmp_path: Path, path: str, data: str
) -> None:
    repo = _repo(tmp_path)
    _stage(repo, path, data)
    _refused_staged_and_tracked(repo, path, REPORT_MESSAGE)


@_path_data_cases(
    [
        ("docs/index.html", "<!DOCTYPE html><html><body>docs</body></html>\n"),
        ("docs/report.html", "<!DOCTYPE html><html><body>a page named report</body></html>\n"),
        ("docs/report-notes.html", "<html></html>\n"),
        ("docs/report-20261026.html", "<html></html>\n"),
        # source and text that merely mention the marker are not reports
        ("src/report.py", f'HEAD = """{MARKER}"""\n'),
        ("docs/contract.md", f"The report holds `{MARKER}` in its head.\n"),
        ("docs/page.html", "<html><body><p>tracer-report is a name</p></body></html>\n"),
        ("docs/data.json", '{"name": "tracer-report"}\n'),
        # the marker beyond a document that does not start as one (a quoted example)
        ("docs/example.txt", f"example:\n<!DOCTYPE html>\n{MARKER}\n"),
    ],
)
def test_guard_does_not_refuse_what_is_not_a_report(tmp_path: Path, path: str, data: str) -> None:
    repo = _repo(tmp_path)
    _stage(repo, path, data)
    result = _guard(repo)
    assert result.returncode == 0, (path, result.stderr)
    assert result.stderr == ""


# ---------------------------------------------------------------------- the stores


@pytest.mark.parametrize(
    "path",
    [
        "out/tracer.sqlite",
        "out/tracer.sqlite-wal",
        "out/tracer.sqlite-shm",
        "out/tracer.sqlite-journal",
        "elsewhere/run1.sqlite",
        "elsewhere/run1.sqlite3",
        "elsewhere/Run1.SQLITE",
    ],
)
def test_guard_refuses_a_store_file_by_its_name_whatever_it_holds(
    tmp_path: Path, path: str
) -> None:
    repo = _repo(tmp_path)
    _stage(repo, path, b"not a database\n")
    result = _guard(repo)
    assert result.returncode == 1
    assert STORE_MESSAGE + path in _lines(result)


@pytest.mark.parametrize("path", ["data/blob.bin", "data/copy-of-store", "data/store.txt"])
def test_guard_refuses_a_renamed_store_by_the_sqlite_header(tmp_path: Path, path: str) -> None:
    repo = _repo(tmp_path)
    _stage(repo, path, SQLITE_MAGIC + b"\x10\x00\x01\x01" + b"\x00" * 4000)
    _refused_staged_and_tracked(repo, path, STORE_MESSAGE)


@_path_data_cases(
    [
        ("docs/sqlite.md", b"The file starts with SQLite format 3 and a NUL byte.\n"),
        ("docs/almost.bin", b" " + SQLITE_MAGIC),  # the header must be the first bytes
        ("docs/short.bin", SQLITE_MAGIC[:-1]),
        ("docs/store-notes.sqlite.md", b"notes\n"),
        ("docs/sqlite-guide.txt", b"notes\n"),
        ("docs/tracer-sqlite.txt", b"notes\n"),
        ("docs/empty.bin", b""),
    ],
)
def test_guard_does_not_refuse_what_is_not_a_store(tmp_path: Path, path: str, data: bytes) -> None:
    repo = _repo(tmp_path)
    _stage(repo, path, data)
    result = _guard(repo)
    assert result.returncode == 0, (path, result.stderr)


# ---------------------------------------------------------------------- the call logs


@pytest.mark.parametrize(
    "path",
    [
        "out/sarvam_call_log.jsonl",
        "sarvam_call_log.jsonl",
        "tools/other/sarvam_call_log.jsonl",
        "tools/capture/sub/sarvam_call_log.jsonl",
    ],
)
def test_guard_refuses_a_call_log_by_its_name(tmp_path: Path, path: str) -> None:
    repo = _repo(tmp_path)
    _stage(repo, path, LEGACY_LINE)  # the name decides, whatever the first line says
    result = _guard(repo)
    assert result.returncode == 1
    assert CALL_LOG_MESSAGE + path in _lines(result)


def test_guard_refuses_a_renamed_call_log_by_the_shape_of_its_first_line(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _stage(repo, "out/run1-log.jsonl", CALL_LOG_LINE + CALL_LOG_LINE)
    _refused_staged_and_tracked(repo, "out/run1-log.jsonl", CALL_LOG_MESSAGE)


def test_guard_keeps_the_capture_tools_own_call_log_that_is_tracked_since_phase_1(
    tmp_path: Path,
) -> None:
    """``tools/capture/sarvam_call_log.jsonl`` is committed (older line shape, no text): the one
    exact path the name rule leaves alone. The same path holding a Tracer call log is refused."""
    repo = _repo(tmp_path)
    _stage(repo, LEGACY_CALL_LOG, LEGACY_LINE * 3)
    assert _guard(repo).returncode == 0
    _git(repo, "commit", "-q", "-m", "legacy")
    assert _guard(repo).returncode == 0, "tracked and in history, still allowed"
    _stage(repo, LEGACY_CALL_LOG, CALL_LOG_LINE)
    result = _guard(repo)
    assert result.returncode == 1
    assert CALL_LOG_MESSAGE + LEGACY_CALL_LOG in _lines(result)


@_path_data_cases(
    [
        ("docs/calls.jsonl", '{"a": 1}\n'),
        ("tests/fixtures/f7_turn_events.jsonl", '{"event": "turn", "bytes_sent": 1}\n'),
        ("docs/call_log.md", "a note on the call log\n"),
        ("docs/notes-about-sarvam_call_log.jsonl.txt", "a note\n"),
    ],
)
def test_guard_does_not_refuse_what_is_not_a_call_log(tmp_path: Path, path: str, data: str) -> None:
    repo = _repo(tmp_path)
    _stage(repo, path, data)
    result = _guard(repo)
    assert result.returncode == 0, (path, result.stderr)


# ---------------------------------------------------------------------- what stays as it was


def test_guard_still_refuses_private_fixtures_and_env_files(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _stage(repo, PRIVATE_PREFIX + "call.txt", "synthetic placeholder, not real data\n")
    _stage(repo, ".env.local", "X=1\n")
    _stage(repo, ".env.example", "X=\n")
    result = _guard(repo)
    assert result.returncode == 1
    lines = _lines(result)
    assert any(line.endswith(PRIVATE_PREFIX + "call.txt") for line in lines)
    assert any(line.endswith(": .env.local") for line in lines)
    assert not any(".env.example" in line for line in lines)


def test_guard_output_names_each_path_once_in_sorted_order(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    for path in ("b/tracer.sqlite", "a/report-20261026T090000Z.html", "c/sarvam_call_log.jsonl"):
        _stage(repo, path, "x\n")
    result = _guard(repo)
    assert result.returncode == 1
    paths = [line.rsplit(": ", 1)[1] for line in _lines(result)]
    assert paths == sorted(paths)
    assert len(paths) == len(set(paths)) == 3
    assert result.stdout == ""


def test_guard_script_is_still_a_standalone_stdlib_script() -> None:
    import ast

    tree = ast.parse(GUARD_SCRIPT.read_text("utf-8"))
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert imported <= set(sys.stdlib_module_names), sorted(imported - set(sys.stdlib_module_names))
    assert not imported & {"socket", "ssl", "http", "urllib", "requests", "httpx"}


# ---------------------------------------------------------------------- .gitignore


def _ignored(path: str) -> bool:
    proc = subprocess.run(  # noqa: S603
        [GIT, "check-ignore", "--no-index", "-q", "--", path], cwd=ROOT, check=False
    )
    assert proc.returncode in (0, 1), proc
    return proc.returncode == 0


@pytest.mark.parametrize(
    "path",
    [
        "out/report-20261026T090000Z.html",
        "deep/er/out/report-20261231T235959Z.html",
        "out/tracer.sqlite",
        "out/tracer.sqlite-wal",
        "out/tracer.sqlite-shm",
        "out/tracer.sqlite-journal",
        "elsewhere/run1.sqlite",
        "elsewhere/run1.sqlite3",
        "out/sarvam_call_log.jsonl",
        "deep/er/out/sarvam_call_log.jsonl",
    ],
)
def test_gitignore_lists_the_outputs_of_tracer_score(path: str) -> None:
    assert _ignored(path), f"{path} is not git-ignored"


@pytest.mark.parametrize(
    "path",
    [
        "docs/examples/rubric-mr.example.json",
        "docs/examples/synthetic-mr/stt_chunk_00.json",
        "docs/examples/synthetic-mr/export_synthetic_mr.txt",
        "docs/index.html",
        "docs/report.html",
        "docs/report-notes.html",
        "packages/cli/src/tracer_cli/report.py",
        "packages/cli/tests/test_e2e_synthetic_mr.py",
        LEGACY_CALL_LOG,
        "packages/evalschema/tests/fixtures/real/pipecat-1.13.0/en/f7_turn_events.jsonl",
    ],
)
def test_gitignore_does_not_hide_ordinary_files(path: str) -> None:
    assert not _ignored(path), f"{path} is ignored: the rule is broader than the outputs"
