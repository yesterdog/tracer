# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
# ruff: noqa: E501
"""P4-J25: the repository guard refuses a staged judge exchange, a staged labels file or packet and
a staged judge allowlist (ADR 0030 rules 14, 19, 22 and 25; BL-142).

The exchange files hold the agent's words and the model's raw replies, the labels and the packets
hold a person's judgement of a call, and the judge allowlist names the endpoints and the key
variables of the owner. All are local and git-ignored; this guard and the ``.gitignore`` entries keep
them out of the repository, as ``test_guard_rejects_staged_report_store_and_call_log`` does for the
outputs of ``tracer score``. The real ``scripts/guard_private_fixtures.py`` runs in a throwaway
repository. Contract: docs/phase4/api-surface-batch-j2.md section 14. The format members are written
in two pieces: the guard refuses a tracked file that holds one as a single string.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from cli_batchf_support import ROOT, git, guard_repo, run_guard  # type: ignore[import-not-found,unused-ignore]

EXCHANGE_MESSAGE = "tracer judge exchange file tracked, staged or in history: "
LABELS_MESSAGE = "tracer judge labels file tracked, staged or in history: "
PACKET_MESSAGE = "tracer judge label packet tracked, staged or in history: "
ALLOWLIST_MESSAGE = "tracer judge allowlist tracked, staged or in history: "

RUN = "run_01a17f10-4888-7637-86a0-1af7ad452c4e"
BUNDLE = "rbnd_01a17f10-4888-7637-86a0-1af7ad452c4e"
HEX = "0123456789abcdef" * 4
EXCHANGE_FORMAT = "tracer.judge_" + "exchange/1"
LABELS_FORMAT = "tracer.judge_" + "labels/1"
PACKET_FORMAT = "tracer.judge_" + "packet/1"
EXCHANGE = json.dumps(
    {
        "format": EXCHANGE_FORMAT,
        "messages": [],
        "response_body": "{}",
        "request_id": None,
        "status": 200,
    }
)
LABELS = json.dumps({"format": LABELS_FORMAT, "bundle_id": BUNDLE, "labeller": "ana", "labels": []})
PACKET = json.dumps({"format": PACKET_FORMAT, "bundle_id": BUNDLE, "items": []}, indent=2)
ENTRY = "https://api.example.test/v1/chat/completions profile=openai/1 key_env=EXAMPLE_JUDGE_KEY\n"
LOOPBACK_ENTRY = (
    "# my endpoints\nhttp://127.0.0.1:8123/v1/chat/completions profile=local_loopback/1\n"
)


def _lines(result: subprocess.CompletedProcess[str]) -> list[str]:
    return result.stderr.splitlines()


def test_guard_rejects_staged_judge_exchanges_labels_and_allowlist(tmp_path: Path) -> None:
    repo = guard_repo(tmp_path)
    assert run_guard(repo).returncode == 0, "a clean repository passes"
    refused: dict[str, tuple[str, str]] = {
        f"out/judge/{RUN}/item-q0-1.json": (EXCHANGE, EXCHANGE_MESSAGE),
        f"out/judge/{BUNDLE}/item-mr_q01-2.json": (EXCHANGE, EXCHANGE_MESSAGE),
        "data/anything.json": (EXCHANGE, EXCHANGE_MESSAGE),  # by content, whatever its name
        f"out/labels/labels-{HEX}.json": (LABELS, LABELS_MESSAGE),
        "elsewhere/ana-labels.json": (LABELS, LABELS_MESSAGE),  # by content
        f"out/labels/packet-{BUNDLE}.json": (PACKET, PACKET_MESSAGE),
        "elsewhere/packet.json": (PACKET, PACKET_MESSAGE),  # by content
        "ops/judge-allowlist.txt": (ENTRY, ALLOWLIST_MESSAGE),
        "ops/judge_allowlist.local": (ENTRY, ALLOWLIST_MESSAGE),
        "ops/endpoints.txt": (LOOPBACK_ENTRY, ALLOWLIST_MESSAGE),  # by content
    }
    allowed: dict[str, str] = {
        # text that merely mentions the formats or the entry shape is not a finding
        "docs/notes.md": (
            "A judge allowlist line is `<url> profile=<name> key_env=<NAME>`, one per line.\n"
            f'The formats are named "{LABELS_FORMAT}" and "{PACKET_FORMAT}" in the ADR.\n'
        ),
        "docs/judge/notes.json": '{"a": 1}',
        "docs/labels/notes.json": '{"a": 1}',
        "packages/x/item-q0-1.json": "{}",
        "docs/schema.json": json.dumps({"enum": [LABELS_FORMAT, PACKET_FORMAT, EXCHANGE_FORMAT]}),
        "tests/test_x.py": (
            "def test_x() -> None:\n"
            f'    line = f"{{url}} profile=openai/1 key_env=SOME_KEY_NAME"  # {LABELS_FORMAT}\n'
        ),
        # text that quotes the member of a labels file or of a packet is not one (only a JSON object is)
        "docs/format-notes.md": f'A labels file has the member "format": "{LABELS_FORMAT}" first.\n',
        "docs/example.diff": f'-        "format": "{PACKET_FORMAT}",\n',
        "docs/howto.md": (
            "Write one line for each endpoint, for example "
            "https://judge.example.test/v1/chat/completions profile=openai/1 key_env=EXAMPLE_NAME "
            "in the file.\n"
        ),
        "docs/judge-allowlist-howto.md": "How to write the allowlist.\n",
        "docs/judge-allowlist.md": "How to write the allowlist.\n",
        "tools/judge_allowlist.py": "ALLOWLIST_HELP = 1\n",
    }
    for path, (data, _message) in refused.items():
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(data, "utf-8")
    for path, data in allowed.items():
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(data, "utf-8")
        git(repo, "add", "--", path)
    assert run_guard(repo).returncode == 0, "untracked files, and text that only names the formats"
    # staged one at a time: exit 1, the path and the kind are named
    for path, (_data, message) in refused.items():
        git(repo, "add", "-f", "--", path)
        result = run_guard(repo)
        assert result.returncode == 1, path
        assert _lines(result) == [message + path], (path, result.stderr)
        git(repo, "rm", "-q", "--cached", "--", path)
        assert run_guard(repo).returncode == 0, f"{path} unstaged again"
    for path in refused:
        git(repo, "add", "-f", "--", path)
    result = run_guard(repo)
    assert result.returncode == 1
    for path, (_data, message) in refused.items():
        assert message + path in _lines(result)
    assert len(_lines(result)) == len(refused)
    paths = [line.rsplit(": ", 1)[1] for line in _lines(result)]
    assert paths == sorted(paths)
    # tracked after a commit: still refused
    git(
        repo,
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@example.test",
        "commit",
        "-q",
        "-m",
        "synthetic",
    )
    assert len(_lines(run_guard(repo))) == len(refused)
    # out of the index but in history: refused by name (a path that is judged by content only is not)
    for path in refused:
        git(repo, "rm", "-q", "--cached", "--", path)
    git(
        repo,
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@example.test",
        "commit",
        "-q",
        "-m",
        "untrack",
    )
    history = run_guard(repo)
    assert history.returncode == 1
    by_name = {
        f"out/judge/{RUN}/item-q0-1.json": EXCHANGE_MESSAGE,
        f"out/judge/{BUNDLE}/item-mr_q01-2.json": EXCHANGE_MESSAGE,
        f"out/labels/labels-{HEX}.json": LABELS_MESSAGE,
        f"out/labels/packet-{BUNDLE}.json": PACKET_MESSAGE,
        "ops/judge-allowlist.txt": ALLOWLIST_MESSAGE,
        "ops/judge_allowlist.local": ALLOWLIST_MESSAGE,
    }
    assert sorted(_lines(history)) == sorted(m + p for p, m in by_name.items())


def test_the_judge_files_are_git_ignored(tmp_path: Path) -> None:
    """The ``.gitignore`` of the repository lists them: the exchange files of a run and of a
    repeat, the labels files and the packets, and a judge allowlist."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".gitignore").write_text((ROOT / ".gitignore").read_text("utf-8"), "utf-8")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)  # noqa: S603, S607
    ignored = [
        f"out/judge/{RUN}/item-q0-1.json",
        f"out/judge/{BUNDLE}/item-mr_q01-2.json",
        f"out/labels/labels-{HEX}.json",
        f"out/labels/packet-{BUNDLE}.json",
        "ops/judge-allowlist.txt",
        "judge-allowlist.local",
        "ops/judge_allowlist.txt",
    ]
    kept = ["docs/judge/README.md", "docs/labels.md", "docs/phase4/api-surface-batch-j2.md"]
    for path in ignored:
        result = subprocess.run(  # noqa: S603
            ["git", "-C", str(repo), "check-ignore", "-q", path],  # noqa: S607
            capture_output=True,
        )
        assert result.returncode == 0, f"{path} is not git-ignored"
    for path in kept:
        result = subprocess.run(  # noqa: S603
            ["git", "-C", str(repo), "check-ignore", "-q", path],  # noqa: S607
            capture_output=True,
        )
        assert result.returncode == 1, f"{path} must not be ignored"
