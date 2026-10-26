# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Fail if a private fixture, a local env file or an output of `tracer score` is tracked, staged or
in history.

The outputs of the command (the HTML report, the SQLite store and the call log) hold personal data
(P3-34). Names are judged for every path of the index, of HEAD and of history. Content is judged for
the paths of the index (staged and tracked) only, on the first 64 KiB of the staged blob: a path
that is only in HEAD or in history is judged by its name alone.
"""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from pathlib import Path

PRIVATE_PREFIX = "packages/evalschema/tests/fixtures/private/"

HEAD_BYTES = 65_536
REPORT_NAME = re.compile(r"report-[0-9]{8}T[0-9]{6}Z\.html")
REPORT_MARKER = re.compile(rb"<meta\s+name\s*=\s*[\"']tracer-report[\"']", re.IGNORECASE)
DOCUMENT_START = re.compile(rb"\A(?:\xef\xbb\xbf)?\s*<(?:!doctype\s+html|html)\b", re.IGNORECASE)
STORE_SUFFIXES = (".sqlite", ".sqlite3")
STORE_SIDECARS = ("-wal", "-shm", "-journal")
SQLITE_MAGIC = b"SQLite format 3\x00"
CALL_LOG_NAME = "sarvam_call_log.jsonl"
# The capture tool's own log, tracked since Phase 1 (an older line shape, no text). One exact path:
# a Tracer call log at this path is still refused by its content.
CALL_LOG_ALLOWED = frozenset({"tools/capture/sarvam_call_log.jsonl"})

PRIVATE = "private fixture or env file"
REPORT = "tracer report file"
STORE = "tracer store file"
CALL_LOG = "tracer call log"


def _is_env_file(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return (name == ".env" or name.startswith(".env.")) and name != ".env.example"


def _git(root: Path, *args: str) -> list[str]:
    proc = subprocess.run(["git", *args], cwd=root, capture_output=True)
    if proc.returncode != 0:
        return []
    return [n for n in proc.stdout.decode("utf-8").split("\0") if n]


def kind_by_name(path: str) -> str | None:
    name = path.rsplit("/", 1)[-1]
    if REPORT_NAME.fullmatch(name):
        return REPORT
    stem = name.lower()
    for sidecar in STORE_SIDECARS:
        if stem.endswith(sidecar):
            stem = stem[: -len(sidecar)]
            break
    if stem.endswith(STORE_SUFFIXES):
        return STORE
    if name == CALL_LOG_NAME and path not in CALL_LOG_ALLOWED:
        return CALL_LOG
    return None


def kind_by_content(path: str, head: bytes) -> str | None:
    if head.startswith(SQLITE_MAGIC):
        return STORE
    if REPORT_MARKER.search(head):
        suffix = path.rsplit("/", 1)[-1].lower().rpartition(".")[2]
        if suffix in {"html", "htm"} or DOCUMENT_START.match(head):
            return REPORT
    if path.lower().endswith(".jsonl"):
        first = head.split(b"\n", 1)[0]
        if b'"bytes_sent"' in first and b'"audio_seconds"' in first:
            return CALL_LOG
    return None


def _index_heads(root: Path, paths: list[str]) -> dict[str, bytes]:
    """The first HEAD_BYTES of the staged blob of every path, through one `git cat-file --batch`."""
    # A path with a line break cannot be sent on the line protocol; its name is still judged.
    wanted = [p for p in paths if "\n" not in p]
    if not wanted:
        return {}
    heads: dict[str, bytes] = {}
    with tempfile.TemporaryFile() as requests:
        requests.write("".join(f":{p}\n" for p in wanted).encode("utf-8"))
        requests.seek(0)
        with subprocess.Popen(
            ["git", "cat-file", "--batch"],
            cwd=root,
            stdin=requests,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        ) as proc:
            stream = proc.stdout
            if stream is None:
                return heads
            for path in wanted:
                fields = stream.readline().split()
                if len(fields) != 3:  # "<name> missing": a gitlink or an unmerged entry
                    continue
                size = int(fields[2])
                head = stream.read(min(size, HEAD_BYTES))
                remaining = size - len(head)
                while remaining > 0:
                    skipped = stream.read(min(remaining, 1 << 20))
                    if not skipped:
                        break
                    remaining -= len(skipped)
                stream.read(1)  # the newline after the blob
                if fields[1] == b"blob":
                    heads[path] = head
    return heads


def findings(root: Path) -> list[tuple[str, str]]:
    """The ``(path, kind)`` pairs to refuse, sorted by path then kind, each once."""
    indexed = set(_git(root, "ls-files", "-z", "--cached"))
    found = indexed | set(_git(root, "diff", "-z", "--cached", "--name-only"))
    found.update(_git(root, "ls-tree", "-r", "-z", "--name-only", "HEAD"))
    found.update(_git(root, "log", "--all", "-z", "--format=", "--name-only"))
    result: set[tuple[str, str]] = set()
    for path in found:
        if path.startswith(PRIVATE_PREFIX) or _is_env_file(path):
            result.add((path, PRIVATE))
        kind = kind_by_name(path)
        if kind is not None:
            result.add((path, kind))
    for path, head in _index_heads(root, sorted(indexed)).items():
        kind = kind_by_content(path, head)
        if kind is not None:
            result.add((path, kind))
    return sorted(result)


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    bad = findings(root)
    for path, kind in bad:
        print(f"{kind} tracked, staged or in history: {path}", file=sys.stderr)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
